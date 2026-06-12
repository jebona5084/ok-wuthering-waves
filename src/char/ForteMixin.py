"""
Shared mixin providing FFT-based forte-bar detection.

Multiple characters (Changli, Zhezhi, Lupa, Ciaccona, Carlotta) all contain
identical copies of ``judge_frequncy_and_amplitude`` and
``calculate_forte_num``.  This mixin centralises that logic so subclasses only
need to inherit ``ForteMixin`` and call ``calculate_forte_num``.
"""
import cv2
import numpy as np

from ok import color_range_to_bound


class ForteMixin:
    """Mixin that provides FFT-based forte-bar segmentation helpers."""

    def _judge_frequency_and_amplitude(self, gray, min_freq, max_freq, min_amp):
        """Return True when the column profile of *gray* shows a stripe pattern
        matching the given frequency/amplitude criteria."""
        height, width = gray.shape[:2]
        if height == 0 or width < 64 or not np.array_equal(np.unique(gray), [0, 255]):
            return False

        profile = np.sum(gray == 255, axis=0).astype(np.float32)
        profile -= np.mean(profile)
        spectrum = np.abs(np.fft.fft(profile))

        best_freq = int(np.argmax(spectrum[1:])) + 1
        best_amp = float(spectrum[best_freq])

        self.logger.debug(f'forte freq={best_freq} amp={best_amp:.1f}')
        return (min_freq <= best_freq <= max_freq) or best_amp >= min_amp

    def calculate_forte_num(self, forte_color, box, num=1, min_freq=39, max_freq=41, min_amp=50):
        """Return the number of filled forte segments (0 … *num*) detected in
        *box* using the supplied colour range and FFT stripe test."""
        cropped = box.crop_frame(self.task.frame)
        lower_bound, upper_bound = color_range_to_bound(forte_color)
        image = cv2.inRange(cropped, lower_bound, upper_bound)

        height, width = image.shape
        step = int(width / num)

        forte = num
        left = step * (forte - 1)
        while forte > 0:
            gray = image[:, left:left + step]
            if self._judge_frequency_and_amplitude(gray, min_freq, max_freq, min_amp):
                break
            left -= step
            forte -= 1

        self.logger.info(f'Frequency analysis forte={forte}')
        return forte
