import os
from pathlib import Path

rbuilders_loc = '/var/www/cran2deb/rep/pool/main'
cache_root = '/var/cache/cran2deb'
which_system = os.environ['CRAN2DEB_SYS']
scm_revision = f"svn:0"
maintainer_c2d = 'cran2deb4ubuntu <cran2deb4ubuntu@gmail.com>'
patch_dir = '/etc/cran2deb/patches'
lintian_dir = '/etc/cran2deb/lintian'
pbuilder_results = Path('/var/cache/cran2deb/results') / which_system


# popoulated by update_cache
inst_pkgs = None
