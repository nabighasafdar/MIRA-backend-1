import base64
import io
import logging
import math
from pathlib import Path
from typing import Optional
from system.browser.profile import ViewportSize
try:
    import imageio.v2 as iio
    import numpy as np
    from imageio.core.format import Format
    from PIL import Image
    IMAGEIO_AVAILABLE = True
except ImportError:
    IMAGEIO_AVAILABLE = False
logger = logging.getLogger(__name__)

def _get_padded_size(size: ViewportSize, macro_block_size: int=16) -> ViewportSize:
    width = int(math.ceil(size['width'] / macro_block_size)) * macro_block_size
    height = int(math.ceil(size['height'] / macro_block_size)) * macro_block_size
    return ViewportSize(width=width, height=height)

class VideoRecorderService:

    def __init__(self, output_path: Path, size: ViewportSize, framerate: int):
        self.output_path = output_path
        self.size = size
        self.framerate = framerate
        self._writer: Optional['Format.Writer'] = None
        self._is_active = False
        self.padded_size = _get_padded_size(self.size)

    def start(self) -> None:
        if not IMAGEIO_AVAILABLE:
            logger.error('MP4 recording requires optional dependencies. Please install them with: pip install "browser-use[video]"')
            return
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = iio.get_writer(str(self.output_path), fps=self.framerate, codec='libx264', quality=8, pixelformat='yuv420p', macro_block_size=None)
            self._is_active = True
            logger.debug(f'Video recorder started. Output will be saved to {self.output_path}')
        except Exception as e:
            logger.error(f'Failed to initialize video writer: {e}')
            self._is_active = False

    def add_frame(self, frame_data_b64: str) -> None:
        if not self._is_active or not self._writer:
            return
        try:
            frame_bytes = base64.b64decode(frame_data_b64)
            with Image.open(io.BytesIO(frame_bytes)) as img:
                if img.size != (self.size['width'], self.size['height']):
                    img = img.resize((self.size['width'], self.size['height']), Image.Resampling.BICUBIC)
                if self.padded_size['width'] != self.size['width'] or self.padded_size['height'] != self.size['height']:
                    new_img = Image.new('RGB', (self.padded_size['width'], self.padded_size['height']), (0, 0, 0))
                    x_offset = (self.padded_size['width'] - self.size['width']) // 2
                    y_offset = (self.padded_size['height'] - self.size['height']) // 2
                    new_img.paste(img, (x_offset, y_offset))
                    img = new_img
                img_array = np.array(img)
            self._writer.append_data(img_array)
        except Exception as e:
            logger.warning(f'Could not process and add video frame: {e}')

    def stop_and_save(self) -> None:
        if not self._is_active or not self._writer:
            return
        try:
            self._writer.close()
            logger.info(f'📹 Video recording saved successfully to: {self.output_path}')
        except Exception as e:
            logger.error(f'Failed to finalize and save video: {e}')
        finally:
            self._is_active = False
            self._writer = None