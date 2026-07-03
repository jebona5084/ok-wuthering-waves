"""Shared helpers for the YOLOv8 echo detectors.

Kept dependency-light (cv2/numpy only) so both OnnxYolo8Detect and
OpenVinoYolo8Detect can import it without pulling in each other's
inference runtime.
"""
from typing import Tuple

import cv2
import numpy as np


def letterbox(img: np.ndarray, new_shape: Tuple[int, int] = (640, 640)) -> Tuple[np.ndarray, Tuple[int, int]]:
    """
    Resize and reshape images while maintaining aspect ratio by adding padding.
    Args:
        img (np.ndarray): Input image to be resized.
        new_shape (Tuple[int, int]): Target shape (height, width) for the image.
    Returns:
        (np.ndarray): Resized and padded image.
        (Tuple[int, int]): Padding values (top, left) applied to the image.
    """
    shape = img.shape[:2]  # current shape [height, width]

    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = (new_shape[1] - new_unpad[0]) / 2, (new_shape[0] - new_unpad[1]) / 2

    if shape[::-1] != new_unpad:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))

    return img, (top, left)
