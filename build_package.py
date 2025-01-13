#!/usr/bin/env python3
import argparse
import os
import subprocess
import re
from typing import Set, Dict, NamedTuple, Optional, List
from types import MappingProxyType
import tempfile
import glob
from collections import defaultdict
import multiprocessing
import sqlite3
import distro
import contextlib

# Third Party
import requests
from rpy2.robjects.packages import importr
import rpy2.robjects


# NOTE: if there are weird dependency problems look in /var/lib/dpkg/status

_frozen_map = MappingProxyType({})
_cran2deb = importr('cran2deb')


_dist_template = """
Origin: {origin}
Codename: rbuilders
Components: main
Architectures: source amd64 arm64
Description: Debian Repository
"""

_ipak_r_method = """
ipak <- function(pkg) {
    new.pkg <- pkg[!(pkg %in% installed.packages()[, "Package"])]

    if (length(new.pkg))
        install.packages(new.pkg, dependencies = TRUE, repos="http://cran.rstudio.com/")

    sapply(pkg, require, character.only = TRUE)
}
"""

_dist_path = "/etc/cran2deb/archive/rep/conf/distributions"

# libc6 (>= 2.4)
_dep_re = re.compile(r"(?P<pkgname>[^ ]+)\s*(?:\((?P<ver_restriction>.*)\))?")

# '3.5.7-0~jessie'
_deb_version_re = re.compile(r'(?P<version>[^-]+)-(?P<build_num>[^~]+)(?:(?P<r_ver>R\d+\.\d+)?~(?P<distribution>.+))?')

# rver: 0.2.20  debian_revision: 2  debian_epoch: 0
_rver_line_re = re.compile(r'rver: (?P<rver>[^ ]+)\s+debian_revision: (?P<debian_revision>[^ ]+)\s+ debian_epoch: (?P<debian_epoch>[^ ]+)')

# version_update:  rver: 0.2.20  prev_pkgver: 0.2.20-1cran2  prev_success: TRUE
_version_update_line_re = re.compile(r'version_update:\s+rver: (?P<rver>[^ ]+)\s+prev_pkgver: (?P<prev_pkgver>[^ ]+)\s+ prev_success: (?P<prev_success>[^ ]+)')

_changelog_first_line = re.compile(r'(?P<pkgname>[^ ]+) \((?P<version>[^)]+)\) (?P<eol>.*)')

_r_version = tuple(subprocess.check_output(["dpkg-query", "--showformat=${Version}", "--show", "r-base-core"]).decode('utf-8').split('.'))
_r_major_minor = f'{_r_version[0]}0'
_distribution = subprocess.check_output(["lsb_release", "-c", "-s"]).decode('utf-8').strip()  # ex: stretch

# _deb_repo_codename = f'{_distribution}-cran{"".join(_r_major_minor)}'
_deb_repo_codename = _distribution

_num_cpus = multiprocessing.cpu_count()

_local_repo_root = '/var/www/cran2deb/rep'
_local_sqlite_path = '/var/cache/cran2deb/cran2deb.db'


class DebVersion(NamedTuple):
    version: str  # includes epoch
    build_num: str


def _get_deb_version(deb_ver: str) -> DebVersion:
    m = _deb_version_re.match(deb_ver)
    assert m, f"unrecognized deb version format: {deb_ver}"
    m = m.groupdict()

    distribution = m.get('distribution')
    r_ver = m.get('r_ver')

    assert not distribution or _distribution == distribution, f"distribution ({distribution}) of {deb_ver} does not match {_distribution}"
    assert not r_ver or r_ver == "".join(_r_major_minor)
    return DebVersion(m['version'], m['build_num'])


def _get_info_from_deb(deb_path: str):
    pkg, version = subprocess.check_output(["dpkg-deb", "-W", "--showformat=${Package}\n${Version}", deb_path]).decode('utf8').splitlines()
    return pkg, version


def _get_name_replacements() -> Dict[str, str]:
    conn = sqlite3.connect(_local_sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT package FROM packages;")

    # {cran_name.lower(): cran_name}
    name_replacements = {row['package'].lower(): row['package'] for row in cur}
    return name_replacements


_cran_module_names = {
    pkg_name.lower(): pkg_name
    for pkg_name in map(str.strip, subprocess.check_output(['r', '-e', 'print(available.packages()[, 0])']).decode('utf-8').splitlines())
    if pkg_name
}


class PkgName:
    _r_cran_prefix = 'r-cran-'
    _r_bioc_prefix = 'r-bioc-'

    _name_replacements = _get_name_replacements()

    def __init__(self, pkg_name: str, force_cran: bool = False):
        self.version: Optional[str] = None
        self.epoch: Optional[int] = None

        if '=' in pkg_name:
            pkg_name, self.version = pkg_name.split("=", 1)
            assert not set(self.version) & {'>', '<', '='}

        if self.version and ':' in self.version:
            self.epoch, self.version = self.version.split(':', 1)
            self.epoch = int(self.epoch)
            if self.epoch == 0:
                self.epoch = None

        if force_cran:
            self.cran_name = self._strip_r_cran_prefix(pkg_name)
            self.deb_name = self._ensure_r_cran_prefix(pkg_name)
        elif pkg_name.startswith(self._r_cran_prefix) or pkg_name.startswith(self._r_bioc_prefix):
            self.deb_name = pkg_name
            self.cran_name = self._strip_r_cran_prefix(pkg_name)
        else:
            self.deb_name = pkg_name
            self.cran_name = None

        if self.cran_name:
            self.cran_name = self._name_replacements.get(self.cran_name, self.cran_name)

    def __repr__(self):
        value = f'deb_name="{self.deb_name}", epoch={self.epoch}'
        if self.cran_name:
            value = f'{value}, cran_name="{self.cran_name}"'
        return f'PkgName({value})'

    def _ensure_r_cran_prefix(self, pkg_name: str):
        if not pkg_name.startswith(self._r_cran_prefix) and not pkg_name.startswith(self._r_bioc_prefix):
            pkg_name = f"{self._r_cran_prefix}{pkg_name}"

        return pkg_name.lower()

    def _strip_r_cran_prefix(self, pkg_name: str):
        if pkg_name.startswith(self._r_cran_prefix):
            pkg_name = pkg_name[len(self._r_cran_prefix):]

        elif pkg_name.startswith(self._r_bioc_prefix):
            pkg_name = pkg_name[len(self._r_bioc_prefix):]

        pkg_name = _cran_module_names.get(pkg_name, pkg_name)
        return pkg_name


def _ensure_epoch(pkg: PkgName):
    print(f"Ensuring epoch: {pkg.epoch}")
    conn = sqlite3.connect(_local_sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # this will set the package epoch
    row = cur.execute("SELECT deb_epoch from builds where package = ? and r_version = ?", [pkg.cran_name, pkg.version]).fetchone()
    if row:
        previous_epoch = row['deb_epoch']
        if previous_epoch == pkg.epoch:
            return

        cur.execute("update builds set deb_epoch=? where package = ? and r_version = ?", [pkg.epoch, pkg.cran_name, pkg.version])
        conn.commit()
        return previous_epoch

    # this will set the global epoch
    row = cur.execute("""
        SELECT version, base_epoch 
        FROM database_versions 
        where base_epoch = (select max(base_epoch) from database_versions)""").fetchone()
    previous_epoch = row['base_epoch']
    if previous_epoch == pkg.epoch:
        return previous_epoch

    cur.execute("update database_versions set base_epoch=? where version = ?", [pkg.epoch, row['version']])
    conn.commit()

    row = cur.execute("SELECT version, base_epoch FROM database_versions where base_epoch = (select max(base_epoch) from database_versions)").fetchone()
    if row['base_epoch'] != pkg.epoch:
        rows = cur.execute("SELECT version, base_epoch FROM database_versions").fetchall()
        assert False

    return previous_epoch


@contextlib.contextmanager
def _epoch_context(pkg: PkgName):
    previous_epoch = _ensure_epoch(pkg) if pkg.epoch is not None else None
    try:
        yield
    finally:
        if previous_epoch is not None:
            _ensure_epoch(previous_epoch)


def _set_package_compile_failed(pkg: PkgName):
    conn = sqlite3.connect(_local_sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    print(f"Setting build success to 0 for package: {pkg}")
    cur.execute("update builds set success = 0 where package=? and r_version = ? and deb_epoch = ?;", [pkg.cran_name, pkg.version, pkg.epoch or 0])
    conn.commit()


def _reset_module(pkg_name: PkgName):
    print(f"Forcing rebuild of {pkg_name}")

    subprocess.check_call(["reprepro", "-b", _local_repo_root, "remove", "rbuilders", pkg_name.cran_name.lower(), pkg_name.deb_name.lower(), f"{pkg_name.deb_name.lower()}-dbgsym"])

    response = requests.get(f"https://deb.fbn.org/remove/{_deb_repo_codename}/{pkg_name.deb_name}")
    assert response.status_code == 200, f"Error removing {pkg_name} from http repo with response code: {response.status_code}"

    response = requests.get(f"https://deb.fbn.org/remove/{_deb_repo_codename}/{pkg_name.deb_name}-dbgsym")
    assert response.status_code == 200, f"Error removing {pkg_name}-dbgsym from http repo with response code: {response.status_code}"

    subprocess.call(["apt-get", "remove", pkg_name.deb_name])  # the package may not exist yet so don't check the error
    subprocess.check_call(["cran2deb", "build_force", pkg_name.cran_name])


def _ensure_old_versions(old_packages: List[PkgName]):
    # Since available.packages will not pick up old packages with older R version dependencies to match
    # the current R version, the user can manually add an entry to the packages to force it
    # example: INSERT OR REPLACE INTO packages (package, latest_r_version) VALUES ('mvtnorm', '1.0-8');
    # So we default the the db_version unless the latest version is available

    # NOTE: to reset the DB after these changes you must run: `cran2deb repopulate`

    # This will fixes issues where a newer version of the package depends on a newer version of R
    conn = sqlite3.connect(_local_sqlite_path)
    conn.row_factory = sqlite3.Row

    scm_revision = rpy2.robjects.r['scm_revision'][0]
    if ".".join(_r_major_minor) not in {"3.4", "3.5", "4.0"}:
        print(f'Unsupported R version: {".".join(_r_version)}')
        return

    print("Checking for old versions")
    info = distro.lsb_release_info()
    system = f"{info['distributor_id'].lower()}-{info['codename']}"

    cur = conn.cursor()
    for pkg in old_packages:
        name = pkg.cran_name
        ver = pkg.version
        cur.execute("SELECT * FROM builds WHERE package=?", [name])
        rows = [row for row in cur]
        conn.commit()

        if rows and rows[0]['r_version'] == ver:
            continue

        # Drop old versions
        cur.execute("""DELETE FROM packages WHERE package=?; """, [name])
        cur.execute("""DELETE FROM builds WHERE package=?; """, [name])

        cur.execute("""INSERT OR REPLACE INTO packages (package, latest_r_version) VALUES (?, ?);""", [name, ver])

        subprocess.check_call(['cran2deb', 'force_version', name, ver])

        if rows:
            _reset_module(pkg)

        cur.execute("""INSERT OR REPLACE INTO builds
            (package, system, r_version, deb_epoch, deb_revision, db_version, success, date_stamp, time_stamp, scm_revision, log) VALUES
            (?, ?, ?, ?, 1, 1, 0, date('now'), strftime('%H:%M:%S.%f', 'now'), ?, '')""", [name, system, ver, pkg.epoch or 0, scm_revision])

        conn.commit()


class DebRepos:
    def __init__(self):
        # {package_name: DebInfo}
        self._http_deb_info: Optional[Dict[str, Set[DebVersion]]] = None
        self._local_deb_info: Optional[Dict[str, Set[DebVersion]]] = None

    def _http_refresh(self):
        subprocess.check_call(['apt-get', 'update'])

        self._http_deb_info: Dict[str, Set[DebVersion]] = defaultdict(set)

        data = requests.get(f"https://deb.fbn.org/list/{_deb_repo_codename}").json()

        for row in data:
            for version in row['versions']:
                deb_ver = _get_deb_version(version)
                self._http_deb_info[row['name']].add(deb_ver)

    def _local_refresh(self):
        output = subprocess.check_output(['reprepro', '-b', _local_repo_root, "-T", "deb", 'list', 'rbuilders']).decode('utf-8')

        self._local_deb_info: Dict[str, Set[DebVersion]] = defaultdict(set)

        for line in output.splitlines():
            # 'rbuilders|main|source: withr 2.1.2-1cran2'
            _, module_ver = line.split(": ", 1)
            module_name, vers_str = module_ver.split(" ", 1)

            deb_ver = _get_deb_version(vers_str)
            self._local_deb_info[module_name].add(deb_ver)

    def local_has_version(self, pkg_name: PkgName, deb_ver: str):
        deb_ver = _get_deb_version(deb_ver)
        versions_available = self._local_deb_info.get(pkg_name.deb_name, _frozen_map)
        has_ver = deb_ver in versions_available
        if not has_ver:
            print(f"pkg: {pkg_name.deb_name} ver: {deb_ver} not available in local versions: {versions_available}")

        return has_ver

    def refresh(self):
        self._http_refresh()
        self._local_refresh()

    def http_has_version(self, pkg_name: PkgName, deb_ver: str):
        if self._http_deb_info is None:
            self.refresh()

        # This should be moved out
        deb_ver = _get_deb_version(deb_ver)
        versions_available = self._http_deb_info.get(pkg_name.deb_name, _frozen_map)
        has_ver = deb_ver in versions_available
        if not has_ver:
            print(f"pkg: {pkg_name.deb_name} ver: {deb_ver} not available in http versions: {versions_available}")

        return has_ver


def _get_build_dependencies(dir_path: str) -> Set[PkgName]:
    p = subprocess.run(["dpkg-checkbuilddeps"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=dir_path)
    stdout = p.stdout.decode('utf-8').splitlines()
    stderr = p.stderr.decode('utf-8').splitlines()

    if p.returncode == 0:
        print(f"found no dsc deps")
        return set()

    assert not stdout, f"Encountered stdout: {stdout}"
    assert len(stderr) == 1, f"Encountered unknown stderr: {stderr}"

    prefix = "dpkg-checkbuilddeps: error: Unmet build dependencies: "
    assert stderr[0].startswith(prefix)
    deps = stderr[0][len(prefix):]
    print(f"found dsc deps: {deps}")
    return {PkgName(pkg_name) for pkg_name in deps.split(" ")}


def _get_install_dependencies(deb_file_path: str) -> Set[PkgName]:
    print(f"Finding dependencies of {deb_file_path}")
    deps = subprocess.check_output(["dpkg-deb", "-W", "--showformat=${Depends}", deb_file_path]).decode('utf-8').split(', ')

    pkg_names = set()
    for dep in deps:
        dep = dep.strip()
        if dep.startswith('r-cran-') or dep.startswith("r-base-") or dep.startswith('r-api') or dep.startswith('r-bioc-'):
            print(f"Skipping dep: {dep}")
            continue

        m = _dep_re.match(dep)
        assert m, f"Unknown dependency type: {dep}"

        m = m.groupdict()

        pkg_name = PkgName(m['pkgname'])
        pkg_names.add(pkg_name)

    return pkg_names


class PackageBuilder:
    def __init__(self):
        self._deb_repos: DebRepos = DebRepos()

    def _install_deps(self, deps: Set[PkgName]):
        if not deps:
            return

        # Ensure all the deps are available via the http deb repo
        for dep in deps:
            if not dep.cran_name:
                continue

            self.build_pkg(dep)

        print(f"Installing apt-get packages: {deps}")

        pkgs = {pkg.deb_name for pkg in deps}
        subprocess.check_call(['apt-get', 'install', '--no-install-recommends', '-y'] + list(pkgs))

    @staticmethod
    def _ensure_distribution_in_changelog(changelog_path: str):
        # This ensures each file is unique in the repo since files for all distributions
        # are stored together so each file needs to be unique
        with open(changelog_path, 'r') as f:
            data = f.read().splitlines()

        m = _changelog_first_line.match(data[0]).groupdict()
        # NOTE: make sure that the suffix will have the correct version comparison, can check with:
        #   dpkg --compare-versions 0.20-41-1cran1~buster gt 0.20-41-1cran1R4.0~buster; echo $? (0 = true)
        # and verify with apt-cache policy r-cran-[name] after pushed to deb repo
        suffix = f"R{'.'.join(_r_major_minor)}~{_distribution}"
        if suffix not in data[0]:
            data[0] = f'{m["pkgname"]} ({m["version"]}{suffix}) {m["eol"]}'

        data = os.linesep.join(data)
        with open(changelog_path, 'w') as f:
            f.write(data)

    def _build_pkg_dsc_and_upload(self, pkg_name: PkgName):
        print(f"Building deb for {pkg_name}")
        dsc_path = _get_pkg_dsc_path(pkg_name)

        with tempfile.TemporaryDirectory() as td:
            subprocess.check_call(["dpkg-source", "-x", dsc_path], cwd=td)

            dirs = glob.glob(f"{td}/*/")
            assert len(dirs) == 1, f"Did not find only one dir in: {td} dirs: {dirs}"

            # Install build dependencies
            deps = _get_build_dependencies(dirs[0])
            self._install_deps(deps)

            subprocess.check_call(["mk-build-deps", "-i", "-r", "-t", "apt-get --no-install-recommends -y"], cwd=dirs[0])
            subprocess.check_call(['dh_makeshlibs', '-a', '-n'])

            # TODO: this needs to be fixed in the modules themselves as this code doesn't
            # debian_shlibs_path = os.path.join(dirs[0], "debian", "shlibs.local")

            if pkg_name.cran_name.lower() in {"rgeos", "sf", "terra"}:
                print("Applying custom FBN patches to rgeos")
                # for some reason dpkg-build does not find geos-config in /usr/local/bin
                if not os.path.exists("/usr/bin/geos-config"):
                    # TODO: add cleanup
                    os.symlink("/usr/local/bin/geos-config", "/usr/bin/geos-config")

                # And for some reason it cannot determine the package of libgeos_c.so.1 belongs to fbn-libgeos
                # TODO: figure out why and impl better fix
                # with open(debian_shlibs_path, "a") as f:
                #     f.write("libgeos_c 1 fbn-libgeos" + os.linesep)

            # if pkg_name.cran_name.lower() == "rnetcdf":
            #     with open(debian_shlibs_path, "a") as f:
            #         f.write("libnetcdf 15 fbn-libnetcdf" + os.linesep)

            if pkg_name.cran_name.lower() in {"rgdal", "sf", "terra"}:
                if not os.path.exists("/usr/bin/gdal-config"):
                    # TODO: add cleanup
                    os.symlink("/usr/local/bin/gdal-config", "/usr/bin/gdal-config")

                # with open(debian_shlibs_path, "a") as f:
                #     f.write("libgdal 26 fbn-libgdal" + os.linesep)

                # with open(debian_shlibs_path, "a") as f:
                #     f.write("libproj 17 fbn-libproj" + os.linesep)

            self._ensure_distribution_in_changelog(os.path.join(dirs[0], "debian", "changelog"))

            subprocess.check_call(['rm', '-rf', '/tmp/last-build'])
            subprocess.check_call(['mkdir', '-p', '/tmp/last-build'])
            subprocess.check_call(['cp', '-R', td, '/tmp/last-build'])

            subprocess.check_call(["debuild", "-us", "-uc"], cwd=dirs[0])

            debs = glob.glob(f"{td}/*.deb")
            assert len(debs) > 0, f"Did not find any debs in: {td}"

            print("Uploading to remote debian repo")
            need_refresh = False
            for deb in debs:
                # Ensure all the install dependencies get upload to the debian repo
                deps = _get_install_dependencies(deb)
                self._install_deps(deps)

                # On the first run cran2deb may not have provided the correct version so we need
                # to check again here
                pkg_name, version = _get_info_from_deb(deb)
                pkg_name = PkgName(pkg_name)
                if self._deb_repos.http_has_version(pkg_name, version):
                    continue

                print(f"Uploading {pkg_name} with ver: {version} from {deb} to: {_deb_repo_codename}")

                response = requests.post(
                    f"https://deb.fbn.org/add/{_deb_repo_codename}",
                    files={'deb-file': (os.path.basename(deb), open(deb, "rb"))})
                assert response.status_code == 200, f"Error with request {response}"

                need_refresh = True
                # Upload deb to local repo
                if not self._deb_repos.local_has_version(pkg_name, version):
                    print(f'Adding {deb} to {_local_repo_root}')
                    # NOTE: if you use with "-b" you'll get an error about not finding conf/distribution
                    subprocess.check_call(['reprepro', '--ignore=wrongdistribution', '--ignore=missingfile', '-b', '.', 'includedeb', 'rbuilders', deb], cwd=_local_repo_root)

            if need_refresh:
                self._deb_repos.refresh()

    # NOTE: this can be recursive
    def build_pkg(self, cran_pkg_name: PkgName, force_build: bool = False):
        local_ver = _get_cran2deb_version(cran_pkg_name)

        print(f"Ensuring Build of {cran_pkg_name} ver: {local_ver}")

        # Unfortunately we can't get the dependencies via apt-cache depends as
        # that module may not match what we're building.  So we need to actually build
        # each module and find the dependencies from the deb file
        # NOTE: if the repo has the module, we're assuming all the dependencies made it as well
        if self._deb_repos.http_has_version(cran_pkg_name, local_ver):
            print(f"HTTP Debian Repo already has version: {local_ver} of {cran_pkg_name}.  Skipping")
            return

        # If our local repo has the deb similarly we assume all the deps made it as well
        if not force_build and self._deb_repos.local_has_version(cran_pkg_name, local_ver):
            print(f"Local Repo already has version: {local_ver} of {cran_pkg_name}.  Skipping")
            return

        # Build source package
        print("Building source package")
        with _epoch_context(cran_pkg_name):
            if force_build:
                _set_package_compile_failed(cran_pkg_name)
            subprocess.check_call(["cran2deb", "build", cran_pkg_name.cran_name])

        # Build deb package
        self._build_pkg_dsc_and_upload(cran_pkg_name)


def _get_pkg_dsc_path(pkg_name: PkgName):
    glob_str = f"/etc/cran2deb/archive/rep/pool/main/{pkg_name.cran_name[0].lower()}/{pkg_name.cran_name.lower()}/*.dsc"
    glob_dscs = glob.glob(glob_str)

    if len(glob_dscs) != 1:
        print(f"Could not find one dsc in: {glob_str} found: {glob_dscs}")
        input("Press Enter to continue...")
        assert False

    return glob_dscs[0]


def _get_cran2deb_version(pkg_name: PkgName):
    """
    On a non-built package output will look like:

    new_build_version:   pkgname: gtable
    rver: 0.3.0  debian_revision: 1  debian_epoch: 0
    0.3.0-1cran1

    On built package output will look like:
    new_build_version:   pkgname: rjson
    rver: 0.2.20  debian_revision: 2  debian_epoch: 0
    version_update:  rver: 0.2.20  prev_pkgver: 0.2.20-1cran2  prev_success: TRUE
    rver: 0.2.20  debian_revision: 3  debian_epoch: 0
    0.2.20-1cran3

    If `version_update` is available, we must use that, otherwise we can assume that
    if `debian_revision` == 1, it's actually 2, otherwise it's correct

    """
    with _epoch_context(pkg_name):
        # tweak of new_build_version
        db_ver = _cran2deb.db_latest_build_version(pkg_name.cran_name)
        if db_ver == rpy2.robjects.rinterface.NULL:
            db_ver = None
        else:
            db_ver = db_ver[0]

        print(f"getting cran version of: {pkg_name}")
        if pkg_name.version:
            latest_r_ver = pkg_name.version
        else:
            latest_r_ver = rpy2.robjects.r.available.rx(pkg_name.cran_name, 'Version')[0]

        if db_ver is not None:
            version = _cran2deb.version_update(latest_r_ver, db_ver, False)[0]  # False since we don't want to increment the cran#
        else:
            version = _cran2deb.version_new(latest_r_ver)[0]

    return version + f"R{'.'.join(_r_major_minor)}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-origin', type=str, default='deb.fbn.org', help='Debian Repo hostname')
    parser.add_argument('-force_build', action='store_true', help='Build + Upload to deb repo even if in local repo')
    parser.add_argument('cran_pkg_name', type=str, nargs='+', help='package to build.  ex: ggplot2.  To force specific version: ggplot2=1.2.3')

    app_args = parser.parse_args()

    os.environ["DEB_BUILD_OPTIONS"] = f'parallel={_num_cpus}'
    os.environ['MAKEFLAGS'] = f'-j{_num_cpus}'

    with open(_dist_path, "w") as f:
        f.write(_dist_template.format(origin=app_args.origin))

    old_packages = []

    if ".".join(_r_major_minor) == "3.4":
        old_packages = [
            PkgName('mvtnorm=1.0-8', True),  # latest mvtnorm is 3.5+
            PkgName('multcomp=1.4-8', True),  # Latest version requires latest mvtnorm which requires newer R version
            PkgName('caret=6.0-81', True),
            PkgName('udunits=1.3.1', True)
        ]

    old_names = {pkg_name.cran_name for pkg_name in old_packages}

    _ensure_old_versions(old_packages)

    pkg_builder = PackageBuilder()

    for cran_pkg_name in app_args.cran_pkg_name:
        cran_pkg_name = PkgName(cran_pkg_name, True)

        assert not cran_pkg_name.version or cran_pkg_name.cran_name not in old_names, f"this package's version is overridden"

        if cran_pkg_name.version:
            _ensure_old_versions([cran_pkg_name])

        pkg_builder.build_pkg(cran_pkg_name, app_args.force_build)


if __name__ == '__main__':
    main()
