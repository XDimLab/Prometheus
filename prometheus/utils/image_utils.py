# python3.9
# pylint: disable=use-implicit-booleaness-not-comparison-to-zero
"""Contains utility functions for image processing.

The module is primarily built on `cv2`. But, differently, we assume all colorful
images are with `RGB` channel order by default. Also, we assume all gray-scale
images to be with shape [height, width, 1].
"""

import os
import warnings
from PIL import Image
import cv2
import numpy as np
import torch
import matplotlib
import matplotlib.pyplot as plt
from einops import rearrange
from .misc import IMAGE_EXTENSIONS
from .misc import check_file_ext

__all__ = [
    'view_color_coded_images', 'concatenate_images', 'get_blank_image', 'load_image', 'save_image', 'resize_image',
    'add_text_to_image', 'preprocess_image', 'postprocess_image',
    'parse_image_size', 'get_grid_shape', 'list_images_from_dir','colorize_depth_maps'
]

def concatenate_images(images, axis='width'):
    if axis == 'width':
        concatenated_images = rearrange(images, 'n h w c-> h (n w) c')
    elif axis == 'height':
        concatenated_images = rearrange(images, 'n h w c -> (n h) w c')
    else:
        raise ValueError("axis must be 'width' or 'height'")

    return concatenated_images

def view_color_coded_images(images, num_rows = -1, num_cols = -1, cmap  = "Spectral", c_range = [0,1]):
    if isinstance(images, list):
        num_frames = len(images)
    else:
        num_frames = images.shape[0]
    
    if num_rows == -1:
        num_rows = 1
        num_cols = num_frames

    figsize = (num_cols * 2, num_rows * 2)
    cmap = plt.get_cmap("Spectral")
    fig, axs = plt.subplots(num_rows, num_cols, figsize=figsize)
    axs = axs.flatten()
    for i in range(num_rows * num_cols):
        if i < num_frames:
            axs[i].imshow(images[i])
            for s in ["bottom", "top", "left", "right"]:
                axs[i].spines[s].set_color(cmap((c_range[1] - c_range[0]) * i / (num_frames) + c_range[0]))
                axs[i].spines[s].set_linewidth(5)
            axs[i].set_xticks([])
            axs[i].set_yticks([])
        else:
            axs[i].axis("off")
    # plt.tight_layout()

    return fig


def _check_2d_image(image):
    """Checks whether a given image is valid.

    A valid image is expected to be with dtype `uint8`. Also, it should have
    shape like:

    (1) (height, width, 1)  # gray-scale image.
    (2) (height, width, 3)  # colorful image.
    (3) (height, width, 4)  # colorful image with transparency (RGBA)
    """
    assert isinstance(image, np.ndarray)
    assert image.dtype == np.uint8
    assert image.ndim == 3 and image.shape[2] in [1, 3, 4]


def get_blank_image(height, width, channels=3, use_black=True):
    """Gets a blank image, either white of black.

    NOTE: This function will always return an image with `RGB` channel order for
    color image and pixel range [0, 255].

    Args:
        height: Height of the returned image.
        width: Width of the returned image.
        channels: Number of channels. (default: 3)
        use_black: Whether to return a black image. (default: True)
    """
    shape = (height, width, channels)
    if use_black:
        return np.zeros(shape, dtype=np.uint8)
    return np.ones(shape, dtype=np.uint8) * 255


def load_image(path):
    """Loads an image from disk.

    NOTE: This function will always return an image with `RGB` channel order for
    color image and pixel range [0, 255].

    Args:
        path: Path to load the image from.

    Returns:
        An image with dtype `np.ndarray`, or `None` if `path` does not exist.
    """
    image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None

    if image.ndim == 2:
        image = image[:, :, np.newaxis]
    _check_2d_image(image)
    if image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    return image


def save_image(path, image):
    """Saves an image to disk.

    NOTE: The input image (if colorful) is assumed to be with `RGB` channel
    order and pixel range [0, 255].

    Args:
        path: Path to save the image to.
        image: Image to save.
    """
    if image is None:
        return

    _check_2d_image(image)
    if image.shape[2] == 1:
        cv2.imwrite(path, image)
    elif image.shape[2] == 3:
        cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    elif image.shape[2] == 4:
        cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA))


def resize_image(image, *args, **kwargs):
    """Resizes image.

    This is a wrap of `cv2.resize()`.

    NOTE: The channel order of the input image will not be changed.

    Args:
        image: Image to resize.
        *args: Additional positional arguments.
        **kwargs: Additional keyword arguments.

    Returns:
        An image with dtype `np.ndarray`, or `None` if `image` is empty.
    """
    if image is None:
        return None

    _check_2d_image(image)
    if image.shape[2] == 1:  # Re-expand the squeezed dim of gray-scale image.
        return cv2.resize(image, *args, **kwargs)[:, :, np.newaxis]
    return cv2.resize(image, *args, **kwargs)


def add_text_to_image(image,
                      text='',
                      position=None,
                      font=cv2.FONT_HERSHEY_TRIPLEX,
                      font_size=1.0,
                      line_type=cv2.LINE_8,
                      line_width=1,
                      color=(255, 255, 255)):
    """Overlays text on given image.

    NOTE: The input image is assumed to be with `RGB` channel order.

    Args:
        image: The image to overlay text on.
        text: Text content to overlay on the image. (default: empty)
        position: Target position (bottom-left corner) to add text. If not set,
            center of the image will be used by default. (default: None)
        font: Font of the text added. (default: cv2.FONT_HERSHEY_TRIPLEX)
        font_size: Font size of the text added. (default: 1.0)
        line_type: Line type used to depict the text. (default: cv2.LINE_8)
        line_width: Line width used to depict the text. (default: 1)
        color: Color of the text added in `RGB` channel order. (default:
            (255, 255, 255))

    Returns:
        An image with target text overlaid on.
    """
    if image is None or not text:
        return image

    _check_2d_image(image)
    cv2.putText(img=image,
                text=text,
                org=position,
                fontFace=font,
                fontScale=font_size,
                color=color,
                thickness=line_width,
                lineType=line_type,
                bottomLeftOrigin=False)
    return image


def preprocess_image(image, min_val=-1.0, max_val=1.0):
    """Pre-processes image by adjusting the pixel range and to dtype `float32`.

    This function is particularly used to convert an image or a batch of images
    to `NCHW` format, which matches the data type commonly used in deep models.

    NOTE: The input image is assumed to be with pixel range [0, 255] and with
    format `HWC` or `NHWC`. The returned image will be always be with format
    `NCHW`.

    Args:
        image: The input image for pre-processing.
        min_val: Minimum value of the output image.
        max_val: Maximum value of the output image.

    Returns:
        The pre-processed image.
    """
    assert isinstance(image, np.ndarray)

    image = image.astype(np.float64)
    image = image / 255.0 * (max_val - min_val) + min_val

    if image.ndim == 3:
        image = image[np.newaxis]
    assert image.ndim == 4 and image.shape[3] in [1, 3, 4]
    return image.transpose(0, 3, 1, 2)


def postprocess_image(image, min_val=-1.0, max_val=1.0, return_list = False, return_PIL = False):
    """Post-processes image to pixel range [0, 255] with dtype `uint8`.

    This function is particularly used to handle the results produced by deep
    models.

    NOTE: The input image is assumed to be with format `NCHW`, and the returned
    image will always be with format `NHWC`.

    Args:
        image: The input image for post-processing.
        min_val: Expected minimum value of the input image.
        max_val: Expected maximum value of the input image.

    Returns:
        The post-processed image.
    """

    if isinstance(image,  torch.Tensor):
        image = image.to(torch.float32).detach().cpu().numpy()
    assert isinstance(image, np.ndarray)

    image = image.astype(np.float64)
    image = (image - min_val) / (max_val - min_val) * 255
    image = np.clip(image + 0.5, 0, 255).astype(np.uint8)

    assert image.ndim == 4 and image.shape[1] in [1, 3, 4]
    image = image.transpose(0, 2, 3, 1)
    if return_list:
        image = [image[i] for i in range(len(image))]
    if return_PIL:
        image = [Image.fromarray(image[i]) for i in range(len(image))]
    return image


def parse_image_size(obj):
    """Parses an object to a pair of image size, i.e., (height, width).

    Args:
        obj: The input object to parse image size from.

    Returns:
        A two-element tuple, indicating image height and width respectively.

    Raises:
        If the input is invalid, i.e., neither a list or tuple, nor a string.
    """
    if obj is None or not obj:
        height = 0
        width = 0
    elif isinstance(obj, int):
        height = obj
        width = obj
    elif isinstance(obj, (list, tuple, str, np.ndarray)):
        if isinstance(obj, str):
            splits = obj.replace(' ', '').split(',')
            numbers = tuple(map(int, splits))
        else:
            numbers = tuple(obj)
        if len(numbers) == 0:
            height = 0
            width = 0
        elif len(numbers) == 1:
            height = int(numbers[0])
            width = int(numbers[0])
        elif len(numbers) == 2:
            height = int(numbers[0])
            width = int(numbers[1])
        else:
            raise ValueError('At most two elements for image size.')
    else:
        raise ValueError(f'Invalid type of input: `{type(obj)}`!')

    return (max(0, height), max(0, width))


def get_grid_shape(size, height=0, width=0, is_portrait=False):
    """Gets the shape of a grid based on the size.

    This function makes greatest effort on making the output grid square if
    neither `height` nor `width` is set. If `is_portrait` is set as `False`, the
    height will always be equal to or smaller than the width. For example, if
    input `size = 16`, output shape will be `(4, 4)`; if input `size = 15`,
    output shape will be (3, 5). Otherwise, the height will always be equal to
    or larger than the width.

    Args:
        size: Size (height * width) of the target grid.
        height: Expected height. If `size % height != 0`, this field will be
            ignored. (default: 0)
        width: Expected width. If `size % width != 0`, this field will be
            ignored. (default: 0)
        is_portrait: Whether to return a portrait size of a landscape size.
            (default: False)

    Returns:
        A two-element tuple, representing height and width respectively.
    """
    assert isinstance(size, int)
    assert isinstance(height, int)
    assert isinstance(width, int)
    if size <= 0:
        return (0, 0)

    if height > 0 and width > 0 and height * width != size:
        height = 0
        width = 0

    if height > 0 and width > 0 and height * width == size:
        return (height, width)
    if height > 0 and size % height == 0:
        return (height, size // height)
    if width > 0 and size % width == 0:
        return (size // width, width)

    height = int(np.sqrt(size))
    while height > 0:
        if size % height == 0:
            width = size // height
            break
        height = height - 1

    return (width, height) if is_portrait else (height, width)


def list_images_from_dir(directory):
    """Lists all images from the given directory.

    NOTE: Do NOT support finding images recursively.

    Args:
        directory: The directory to find images from.

    Returns:
        A list of sorted filenames, with the directory as prefix.
    """
    image_list = []
    for filename in os.listdir(directory):
        if check_file_ext(filename, *IMAGE_EXTENSIONS):
            image_list.append(os.path.join(directory, filename))
    return sorted(image_list)


# @catch_warnings
def colorize_depth_maps(
    depth_map, min_depth = None, max_depth = None, cmap="Spectral", valid_mask=None
):
    """
    Input shape [B, H, W] or [H, W] -> No 'C'
    Colorize depth maps.
    Code borrow from Marigold
    """
    assert len(depth_map.shape) >= 2, "Invalid dimension"

    if isinstance(depth_map, torch.Tensor):
        depth = depth_map.to(torch.float32).detach().cpu().squeeze().numpy()
    elif isinstance(depth_map, np.ndarray):
        depth = depth_map.copy().squeeze()
    # reshape to [ (B,) H, W ]
    if depth.ndim < 3:
        depth = depth[np.newaxis, :, :]
    # colorize
    cm = matplotlib.colormaps[cmap]
    if (min_depth is None) or (max_depth is None):
        min_depth = np.min(depth.reshape(depth.shape[0], -1), axis=1).astype(depth.dtype)[:,None,None]
        max_depth = np.max(depth.reshape(depth.shape[0], -1), axis=1).astype(depth.dtype)[:,None,None]
    
    depth_ = ((depth - min_depth) / (max_depth - min_depth)).clip(0, 1) * 255.0
    depth = depth_.astype(np.uint8)
    img_colored_np = cm(depth)[:, :, :, 0:3]  # value from 0 to 1
    img_colored_np = np.rollaxis(img_colored_np, 3, 1)

    if valid_mask is not None:
        if isinstance(depth_map, torch.Tensor):
            valid_mask = valid_mask.detach().numpy()
        valid_mask = valid_mask.squeeze()  # [H, W] or [B, H, W]
        if valid_mask.ndim < 3:
            valid_mask = valid_mask[np.newaxis, np.newaxis, :, :]
        else:
            valid_mask = valid_mask[:, np.newaxis, :, :]
        valid_mask = np.repeat(valid_mask, 3, axis=1)
        img_colored_np[~valid_mask] = 0

    if isinstance(depth_map, torch.Tensor):
        img_colored = torch.from_numpy(img_colored_np).to(depth_map)
    elif isinstance(depth_map, np.ndarray):
        img_colored = img_colored_np

    return img_colored

