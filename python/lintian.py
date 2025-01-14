import os
import logging
import shutil

from .globals import lintian_dir

_logger = logging.getLogger(__name__)

def generate_lintian(pkg):
    lintian_src = os.path.join(lintian_dir, pkg['name'])
    if not os.path.exists(lintian_src):
        _logger.info('no lintian overrides %s', lintian_src)
        return

    # copy the lintian file
    _logger.info('including lintian file %s', lintian_src)
    lintian_tgt = pkg['debfile'](f"{pkg['debname']}.lintian-overrides")
    shutil.copy(lintian_src, lintian_tgt)
