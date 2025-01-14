import os
import shutil
import subprocess
import tempfile
import datetime
import re
import logging

import pandas as pd

from .debiannaming import repourl_as_debian, pkgname_as_debian
from .globals import rbuilders_loc


_logger = logging.getLogger(__name__)


curl_maxtime = 60 * 60  # 60 minutes max download time
curl_retries = 0  # No retries


def setup():
    # set up the working directory
    tmp = tempfile.mkdtemp(prefix='cran2deb')
    return tmp


def cleanup(dir):
    # remove the working directory
    shutil.rmtree(dir)


def download_pkg(dir, pkgname, available):
    # download pkgname into dir, and construct some metadata
    pkg = {}
    pkg['date_stamp'] = datetime.datetime.now()
    pkg['name'] = pkgname
    pkg['version'] = available.loc[pkgname, 'Version']
    pkg['repoURL'] = available.loc[pkgname, 'Repository']
    pkg['repo'] = repourl_as_debian(pkg['repoURL'])
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9+.-]+$', pkg['name']):
        raise Exception('Cannot convert package name into a Debian name', pkg['name'])

    pkg['srcname'] = pkgname_as_debian(pkg['name'], binary=False)
    pkg['debname'] = pkgname_as_debian(pkg['name'], repo=pkg['repo'])

    debfn = os.path.join(rbuilders_loc, f"{pkg['srcname'][0]}/{pkg['srcname']}/{pkg['srcname']}_{pkg['version']}.orig.tar.gz")
    pkg['need_repack'] = False
    if os.path.exists(debfn):
        pkg['archive'] = os.path.join(dir, os.path.basename(debfn))
        shutil.copy(debfn, pkg['archive'])
        pkg['path'] = os.path.join(dir, f"{pkg['srcname']}-{pkg['version']}")
        _logger.info('using an existing debianized source tarball:', debfn)
    else:
        use_local = False
        if pkg['repo'] == 'cran':
            localfn = f"/srv/R/Repositories/CRAN/src/contrib/{pkg['name']}_{pkg['version']}.tar.gz"
            use_local = os.path.exists(localfn)
        elif pkg['repo'] == 'bioc':
            localfn = f"/srv/R/Repositories/Bioconductor/release/bioc/src/contrib/{pkg['name']}_{pkg['version']}.tar.gz"
            use_local = os.path.exists(localfn)

        fn = f"{pkgname}_{pkg['version']}.tar.gz"
        archive = os.path.join(dir, fn)

        if use_local:
            shutil.copy(localfn, archive)
        else:
            if pd.isna(available.loc[pkgname, 'NeedsCompilation']):
                url = f"{available.loc[pkgname, 'Repository']}/Archive/{pkg['name']}/{fn}"
            else:
                url = f"{available.loc[pkgname, 'Repository']}/{fn}"

            _logger.info('Downloading archive ', url)
            ret = subprocess.run(['curl', '--fail', '-o', archive, f"-m {curl_maxtime}", f"--retry {curl_retries}", url])
            if ret.returncode != 0:
                raise Exception('failed to download', url)

        if '..' in archive or os.path.normpath(archive) != archive:
            raise Exception('funny looking path', archive)

        pkg['path'] = re.sub(r"_\.(zip|tar\.gz)", "", re.sub(r"\.tar\.gz$", "", archive))
        pkg['archive'] = archive
        pkg['need_repack'] = True

    return pkg


def repack_pkg(pkg):
    _logger.info('repacking into debian source archive.')
    debpath = os.path.join(os.path.dirname(pkg['archive']), f"{pkg['srcname']}-{pkg['version']}")
    os.rename(pkg['path'], debpath)
    pkg['path'] = debpath
    debarchive = os.path.join(os.path.dirname(pkg['archive']), f"{pkg['srcname']}_{pkg['version']}.orig.tar.gz")
    os.chdir(os.path.dirname(pkg['path']))
    subprocess.run(['find', os.path.basename(pkg['path']), '-type', 'f', '!', '-name', 'configure', '!', '-name', 'cleanup', '-exec', 'chmod', '-x', '{}', ';'])
    if os.path.exists(os.path.join(os.path.basename(pkg['path']), 'debian')):
        _logger.warning('debian/ directory found in tarball! removing...')
        shutil.rmtree(os.path.join(os.path.basename(pkg['path']), 'debian'))
    os.rename(pkg['archive'], debarchive)
    pkg['archive'] = debarchive
    pkg['need_repack'] = False
    return pkg


def prepare_pkg(dir, pkgname, available):
    pkg = download_pkg(dir, pkgname, available)
    if not re.search(r'\.tar\.gz$', pkg['archive']):
        raise Exception('archive is not tarball')

    os.chdir(dir)
    ret = subprocess.run(['tar', 'xzf', pkg['archive']])
    if ret.returncode != 0:
        raise Exception('Extraction of archive', pkg['archive'], 'failed.')

    if pkg['need_repack']:
        pkg = repack_pkg(pkg)
    if not os.path.isdir(pkg['path']):
        raise Exception(pkg['path'], 'is not a directory and should be.')

    pkg['description'] = read_dcf(os.path.join(pkg['path'], 'DESCRIPTION'))
    if 'Version' in pkg['description']:
        if pkg['description']['Version'] != available.loc[pkg['name'], 'Version']:
            _logger.error('available version:', available.loc[pkg['name'], 'Version'])
            _logger.error('package version:', pkg['description']['Version'])
            raise Exception('inconsistency between R package version and cached R version')

    if pkg['description']['Package'] != pkg['name']:
        raise Exception('package name mismatch')

    return pkg
