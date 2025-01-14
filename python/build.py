import os
import subprocess
import logging

from .version import new_build_version
from .getrpkg import setup, prepare_pkg
from .db import db_blacklist_packages
from .debianpkg import prepare_new_debian
from .globals import pbuilder_results

_logger = logging.getLogger(__name__)


def build(name, extra_deps, force=False, do_cleanup=True):
    if name in base_pkgs:
        return True

    dir = setup()

    try:
        version = new_build_version(name)
    except Exception as e:
        _logger.error(f'failed to build in new_build_version: {name}')
        return None

    if not force and not needs_build(name, version):
        _logger.info(f'skipping build of {name}')
        return None

    if name in db_blacklist_packages():
        _logger.info(f'package {name} is blacklisted. consult database for reason.')
        return None

    pkg = prepare_new_debian(prepare_pkg(dir, name), extra_deps)
    if pkg['debversion'] != version:
        raise Exception(f'expected Debian version {version} not equal to actual version {pkg["debversion"]}')

    for file in os.listdir(pbuilder_results):
        if file.endswith('.upload'):
            os.remove(os.path.join(pbuilder_results, file))

    _logger.info(f'R dependencies: {", ".join(pkg["depends"]["r"])}')

    build_debian(pkg)

    _logger.info("Package upload")
    deb_version = pkg['debversion'].split(':')[-1]
    cmd = f'umask 002; cd /var/www/cran2deb/rep && reprepro --ignore=wrongdistribution --ignore=missingfile -b . include rbuilders {changesfilesrc(pkg["srcname"], deb_version, dir)}'
    _logger.info(f'Executing: {cmd}')
    ret = log_system(cmd)
    if ret != 0:
        _logger.info("Upload failed, ignored.")
    else:
        _logger.info("Upload successful.")

    if do_cleanup:
        cleanup(dir)
    else:
        _logger.info(f'output is in {dir}. you must clean this up yourself.')

    failed = isinstance(result, Exception)
    if failed:
        error(f'failure of {name} means these packages will fail: {", ".join(r_dependency_closure(name, forward_arcs=False))}')
    db_record_build(name, version, log_retrieve(), not failed)
    return not failed

def needs_build(name, version):
    build = db_latest_build(name)
    if build and build['success']:
        if (build['r_version'] == version_upstream(version) and
            build['deb_epoch'] == version_epoch(version) and
            build['db_version'] == db_get_version()):
            return False
    else:
        _logger.info(f'rebuilding {name}: no build record or previous build failed')
        return True

    srcname = pkgname_as_debian(name, binary=False)
    debname = pkgname_as_debian(name, binary=True)
    if os.path.exists(changesfile(srcname, version)):
        notice(f'already built {srcname} version {version}')
        return False

    if build['r_version'] != version_upstream(version):
        _logger.info(f'rebuilding {name}: new upstream version {build["r_version"]} (old) vs {version_upstream(version)} (new)')
    if build['deb_epoch'] != version_epoch(version):
        _logger.info(f'rebuilding {name}: new cran2deb epoch {build["deb_epoch"]} (old) vs {version_epoch(version)} (new)')
    if build['db_version'] != db_get_version():
        _logger.info(f'rebuilding {name}: new db version {build["db_version"]} (old) vs {db_get_version()} (new)')
    _logger.info(f'Now deleting {debname}, {srcname}.')
    return True

def build_debian(pkg):
    wd = os.getcwd()
    os.chdir(pkg['path'])

    _logger.info(f'building Debian source package {pkg["debname"]} ({pkg["debversion"]}) in {os.getcwd()} ...')

    cmd = 'debuild -us -uc -sa -S -d'
    if version_revision(pkg['debversion']) > 2:
        cmd += ' -sd'
        _logger.info('build should exclude original source')
    _logger.info(f'Executing "{cmd}" from directory "{os.getcwd()}".')
    ret = log_system(cmd)
    os.chdir(wd)
    if ret != 0:
        raise Exception('Failed to build source package.')
    return ret

def changesfilesrc(srcname, version='*', dir):
    return os.path.join(dir, f'{srcname}_{version}_source.changes')

def needs_upload(name, version):
    debname = pkgname_as_debian(name, binary=True)
    ubuntu_version = subprocess.check_output(['lsb_release', '-c']).decode().split('\t')[1].strip()
    cmd = f'apt-cache show --no-all-versions {debname} | grep Version'
    aval_version = subprocess.check_output(cmd, shell=True).decode().strip()
    u_version = version.split('cran')[0]
    if aval_version:
        aval_version = aval_version.split(ubuntu_version)[0].split(': ')[1].split('cran')[0]
        if u_version.replace('-', '.') == aval_version.replace('-', '.'):
            _logger.info(f'Current version of {name} exists in MAIN, CRAN, or PPA')
            return False
        else:
            _logger.info(f'Older version of {name} exists in MAIN, CRAN, or PPA')
            return True
    else:
        _logger.info(f'No version of {name} exists in MAIN, CRAN, or PPA')
        return True
