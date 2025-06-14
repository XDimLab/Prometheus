"""
Last modified: 2024-09-03
Co3Dv2 Dataset borrow from FlowMap
https://github.com/dcharatan/flowmap/blob/main/flowmap/dataset/dataset_co3d.py
"""

import gzip
import json
from collections import defaultdict
from functools import cache
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Literal
from pathlib import Path

import torch
import torchvision.transforms as tf
from jaxtyping import Float
from PIL import Image
from torch import Tensor
from torch.utils.data import Dataset, default_collate
from tqdm import tqdm

import torch
from jaxtyping import Int64
from torch import Tensor

T = TypeVar("T")

# from ..frame_sampler.frame_sampler import FrameSampler
# from ..misc.cropping import resize_to_cover_with_intrinsics
# from .dataset import DatasetCfgCommon
# from .types import Stage

Frame = tuple[int, Path]
Stage = Literal["train", "test", "val"]

from dataclasses import dataclass


#--------------Crop Function Related----------------------
    

def center_crop_intrinsics(
    intrinsics: Float[Tensor, "*#batch 3 3"] | None,
    old_shape: tuple[int, int],
    new_shape: tuple[int, int],
) -> Float[Tensor, "*batch 3 3"] | None:
    """Modify the given intrinsics to account for center cropping."""

    if intrinsics is None:
        return None

    h_old, w_old = old_shape
    h_new, w_new = new_shape
    intrinsics = intrinsics.clone()
    intrinsics[..., 0, 0] *= w_old / w_new  # fx
    intrinsics[..., 1, 1] *= h_old / h_new  # fy
    return intrinsics



def resize_to_cover(
    image: Image.Image,
    shape: tuple[int, int],
) -> tuple[
    Image.Image,  # the image itself
    tuple[int, int],  # image shape after scaling, before cropping
]:
    w_old, h_old = image.size
    h_new, w_new = shape

    # Figure out the scale factor needed to cover the desired shape with a uniformly
    # scaled version of the input image. Then, resize the input image.
    scale_factor = max(h_new / h_old, w_new / w_old)
    h_scaled = round(h_old * scale_factor)
    w_scaled = round(w_old * scale_factor)
    image_scaled = image.resize((w_scaled, h_scaled), Image.LANCZOS)

    # Center-crop the image.
    x = (w_scaled - w_new) // 2
    y = (h_scaled - h_new) // 2
    image_cropped = image_scaled.crop((x, y, x + w_new, y + h_new))
    return image_cropped, (h_scaled, w_scaled)


def resize_to_cover_with_intrinsics(
    images: list[Image.Image],
    shape: tuple[int, int],
    intrinsics: Float[Tensor, "*batch 3 3"] | None,
) -> tuple[
    list[Image.Image],  # cropped images
    Float[Tensor, "*batch 3 3"] | None,  # intrinsics, adjusted for cropping
]:
    scaled_images = []
    for image in images:
        image, old_shape = resize_to_cover(image, shape)
        scaled_images.append(image)

    if intrinsics is not None:
        intrinsics = center_crop_intrinsics(intrinsics, old_shape, shape)

    return scaled_images, intrinsics

#---------------------
class FrameSampler(ABC, Generic[T]):
    """A frame sampler picks the frames that should be sampled from a dataset's video.
    It makes sense to break the logic for frame sampling into an interface because
    pre-training and fine-tuning require different frame sampling strategies (generally,
    whole video vs. batch of video segments of same length).
    """

    cfg: T

    def __init__(self, cfg: T) -> None:
        self.cfg = cfg

    @abstractmethod
    def sample(
        self,
        num_frames_in_video: int,
        device: torch.device,
    ) -> Int64[Tensor, " frame"]:  # frame indices
        pass
#-----------------------------------------------

@dataclass
class DatasetCfgCommon:
    image_shape: tuple[int, int] | None
    scene: str | None
    
@dataclass
class Sequence:
    name: str
    category: str
    frames: list[Frame]
    viewpoint_quality_score: float | None


@dataclass
class DatasetCO3DCfg(DatasetCfgCommon):
    name: Literal["co3d"]
    root: Path
    set_list: str
    categories: list[str] | None
    load_cameras: bool
    load_frame_paths: bool


class DatasetCO3D(Dataset):
    cfg: DatasetCO3DCfg
    stage: Stage
    sequences: list[Sequence]
    to_tensor: tf.ToTensor
    frame_sampler: FrameSampler

    def __init__(
        self,
        cfg: DatasetCO3DCfg,
        stage: Stage,
        frame_sampler: FrameSampler,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.stage = stage
        self.sequences = []
        self.to_tensor = tf.ToTensor()
        self.frame_sampler = frame_sampler
        self.load_sequences()

    def load_category_sequences(self, category_path: Path) -> list[Sequence]:
        # Instead of reading the set lists, just load all frames.
        sequences = {}
        for example in category_path.iterdir():
            # Skip files and folders that aren't examples.
            if not example.is_dir() or not (example / "images").exists():
                continue

            sequence = []
            for frame in sorted((example / "images").iterdir()):
                assert frame.name.startswith("frame") and frame.suffix == ".jpg"
                index = int(frame.stem[5:])
                sequence.append((index, frame))

            sequences[example.name] = sequence

        # Generate sequence structs.
        sequences = [
            Sequence(name, category_path.name, frames, None)
            for name, frames in sequences.items()
        ]

        # Load the sequence annotations.
        sequence_annotations = json.loads(
            gzip.GzipFile(category_path / "sequence_annotations.jgz", "rb")
            .read()
            .decode("utf8")
        )
        sequence_annotations = {
            annotation["sequence_name"]: annotation
            for annotation in sequence_annotations
        }

        # Add viewpoint quality scores.
        valid_sequences = []
        for sequence in sequences:
            annotations = sequence_annotations[sequence.name]
            score = annotations.get("viewpoint_quality_score", None)
            if score is not None:
                sequence.viewpoint_quality_score = score
                sequence.frames = sorted(sequence.frames)
                valid_sequences.append(sequence)

        return valid_sequences

    def load_sequences(self):
        num_skipped = 0
        categories = [
            dir
            for dir in self.cfg.root.iterdir()
            if dir.is_dir() and not dir.name.startswith(".")
        ]

        # Only load configuration-defined categories.
        if self.cfg.categories is not None:
            categories = [
                category
                for category in categories
                if category.name in self.cfg.categories
            ]

        for category_root in tqdm(categories, desc="Loading CO3D sequences"):
            # Read the set list.
            sequences = self.load_category_sequences(category_root)

            # Filter out sequences with incomplete camera information.
            if self.cfg.load_cameras:
                annotations = self.load_frame_annotations(category_root.name)
                for sequence in sequences:
                    for index, _ in sequence.frames:
                        if annotations.get(sequence.name, []).get(index, None) is None:
                            num_skipped += 1
                            break
                    else:
                        self.sequences.append(sequence)
                print(
                    f"[CO3D] Skipped {num_skipped} sequences. Kept "
                    f"{len(self.sequences)} sequences."
                )
            else:
                self.sequences.extend(sequences)

        if self.cfg.scene is not None:
            self.sequences = [s for s in self.sequences if s.name == self.cfg.scene]

    @cache
    def load_frame_annotations(self, category: str):
        frame_annotations = json.loads(
            gzip.GzipFile(self.cfg.root / category / "frame_annotations.jgz", "rb")
            .read()
            .decode("utf8")
        )

        annotations = defaultdict(dict)

        # Extract camera parameters.
        for frame_annotation in frame_annotations:
            sequence = frame_annotation["sequence_name"]
            frame = frame_annotation["frame_number"]
            annotations[sequence][frame] = {
                **frame_annotation["viewpoint"],
                **frame_annotation["image"],
            }

        return dict(annotations)

    def read_camera_parameters(
        self,
        sequence: Sequence,
        frame_index_in_sequence: int,
    ) -> tuple[
        Float[Tensor, "4 4"],  # extrinsics
        Float[Tensor, "3 3"],  # intrinsics
    ]:
        annotations = self.load_frame_annotations(sequence.category)

        index, _ = sequence.frames[frame_index_in_sequence]
        annotation = annotations[sequence.name][index]

        # Process the intrinsics.
        p = annotation["principal_point"]
        f = annotation["focal_length"]
        h, w = annotation["size"]
        assert annotation["intrinsics_format"] == "ndc_isotropic"
        k = torch.eye(3, dtype=torch.float32)
        s = min(h, w) / 2
        k[0, 0] = f[0] * s
        k[1, 1] = f[1] * s
        k[0, 2] = -p[0] * s + w / 2
        k[1, 2] = -p[1] * s + h / 2
        k[:2] /= torch.tensor([w, h], dtype=torch.float32)[:, None]

        # Process the extrinsics.
        w2c = torch.eye(4, dtype=torch.float32)
        w2c[:3, :3] = torch.tensor(annotation["R"], dtype=torch.float32).T
        w2c[:3, 3] = torch.tensor(annotation["T"], dtype=torch.float32)
        flip_xy = torch.diag_embed(torch.tensor([-1, -1, 1, 1], dtype=torch.float32))
        w2c = flip_xy @ w2c
        c2w = w2c.inverse()

        return c2w, k

    def read_image(
        self,
        sequence: Sequence,
        frame_index_in_sequence: int,
    ):
        result = {}

        # Read the image.
        _, path = sequence.frames[frame_index_in_sequence]
        image = Image.open(path)

        # Load the camera metadata.
        if self.cfg.load_cameras:
            c2w, k = self.read_camera_parameters(sequence, frame_index_in_sequence)
            result["extrinsics"] = c2w
            result["intrinsics"] = k
        else:
            k = None

        # Resize the image to the desired shape.
        if self.cfg.image_shape is not None:
            image, k = resize_to_cover_with_intrinsics([image], self.cfg.image_shape, k)
            image = image[0]

        return {
            "videos": self.to_tensor(image),
            "indices": torch.tensor(frame_index_in_sequence),
            "frame_paths": str(path),
            **result,
        }

    def __len__(self) -> int:
        return len(self.sequences)

    def __getitem__(self, index: int):
        sequence = self.sequences[index]
        indices = self.frame_sampler.sample(len(sequence.frames), torch.device("cpu"))
        example = [self.read_image(sequence, index.item()) for index in indices]
        example = default_collate(example)

        extra = ("frame_paths",) if self.cfg.load_frame_paths else ()
        result = {
            k: example[k]
            for k in ("videos", "indices", "extrinsics", "intrinsics", *extra)
            if k in example
        }
        result["scenes"] = f"{sequence.category}/{sequence.name}"
        result["datasets"] = "co3d"

        return result
    

if __name__ == "__main__":
    cfg = DatasetCO3DCfg(
        scene = None,
        image_shape = None,
        name = Literal["co3d"],
        set_list = 'set_lists_fewview',
        root =  '/nas1/datasets/CO3D_V2',
        categories = ['hydrant', 'apple'],
        load_cameras = True,
        load_frame_paths = True,
    )
    dataset = DatasetCO3D(cfg)
    print('???')

