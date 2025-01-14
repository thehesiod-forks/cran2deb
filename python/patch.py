import os
import shutil
import logging

from .globals import patch_dir

_logger = logging.getLogger(__name__)


def apply_patches(pkg):
    patch_path = os.path.join(patch_dir, pkg['name'])
    if not os.path.exists(patch_path):
        _logger.info('no patches in', patch_path)
        return

    # make debian/patches for simple-patchsys
    deb_patch = pkg['debfile']('patches')
    if not os.path.exists(deb_patch):
        os.makedirs(deb_patch)
    else:
        raise Exception('could not create patches directory', deb_patch)

    # now just copy the contents of patch_path into debian/patches
    for patch in os.listdir(patch_path):
        _logger.info('including patch', patch)
        shutil.copy(os.path.join(patch_path, patch), deb_patch)
