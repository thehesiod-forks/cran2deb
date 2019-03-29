#!/usr/bin/env python3
import argparse
import os
import subprocess
import re
from typing import Set, Dict
import tempfile
import glob
from collections import defaultdict
import multiprocessing

# Third Party
import requests


_dist_template = """
Origin: {origin}
Codename: rbuilders
Components: main
Architectures: source
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
_dep_re = re.compile(r"\s*(?P<deptype>[^:]+):\s*(?P<pkgname>.*)")

# '3.5.7-0~jessie'
_deb_version_re = re.compile(r'(?P<version>[^-]+)-(?P<build_num>[^~]+)(?:~(?P<distribution>.*))?')

_distribution = subprocess.check_output(["lsb_release", "-c", "-s"]).decode('utf-8').strip()

_num_cpus = multiprocessing.cpu_count()


class HttpDebRepo:
    def __init__(self):
        # {package_name: DebInfo}
        self._deb_info: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))

        data = requests.get(f"https://deb.fbn.org/list/{_distribution}").json()

        for row in data:
            for version in row['versions']:
                m = _deb_version_re.match(version)
                assert m, f"unrecognized entry: {row}"
                m = m.groupdict()
                assert not m.get('distribution') or m['distribution'] == _distribution, f"entry {row} does not match distribution: {_distribution}"
                self._deb_info[row['name']][m['version']].add(m['build_num'])


def _get_dependencies(cran_pkg_name: str):
    print("Finding dependencies")
    r_cran_name = f"r-cran-{cran_pkg_name}"
    output = subprocess.check_output(["apt-cache", "depends", r_cran_name]).decode('utf-8')

    depends = set()
    for line in output.splitlines():
        if line.strip() == r_cran_name:
            continue

        m = _dep_re.match(line)
        assert m, f"Unknown line: {line}, with cran_name: {r_cran_name}"

        m = m.groupdict()
        if m['deptype'] == "Suggests":
            continue

        assert m['deptype'] == "Depends", f"Unknown deptype for line: {line}"

        if m['pkgname'] in {'r-base-core'} or m['pkgname'].startswith('<r-api'):
            print(f"Skipping dep: {m['pkgname']}")
            continue

        assert m['pkgname'].startswith("r-cran-")

        depends.add(m['pkgname'].replace("r-cran-", "", 1))

    return depends


def _install_r_deps(deps: Set[str]):
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file = os.path.join(temp_dir, "install_pkgs.R")
        quoted_deps = [f'"{dep}"' for dep in deps]
        contents = _ipak_r_method + f'ipak(c({", ".join(quoted_deps)}))'

        with open(temp_file, "w") as f:
            f.write(contents)

        print(f"running: Rscript against: {contents}")

        subprocess.check_call(["Rscript", temp_file])


def _get_pkg_dsc_path(pkg_name: str):
    glob_str = f"/etc/cran2deb/archive/rep/pool/main/{pkg_name[0]}/{pkg_name}/*.dsc"
    glob_dscs = glob.glob(glob_str)
    assert len(glob_dscs) == 1, f"Could not find one dsc in: {glob_str}"

    return glob_dscs[0]


def _build_pkg_dsc(pkg_name: str):
    print(f"Building deb for {pkg_name}")
    dsc_path = _get_pkg_dsc_path(pkg_name)

    with tempfile.TemporaryDirectory() as td:
        subprocess.check_call(["dpkg-source", "-x", dsc_path], cwd=td)

        dirs = glob.glob(f"{td}/*/")
        assert len(dirs) == 1, f"Did not find only one dir in: {td}"

        subprocess.check_call(["debuild", "-us", "-uc"], cwd=dirs[0])

        debs = glob.glob(f"{td}/*.deb")
        assert len(debs) == 1, f"Did not find only one deb in: {td}"

        print("Uploading to debian repo")
        response = requests.post(
            f"https://deb.fbn.org/add/{_distribution}",
            files={'deb-file': (os.path.basename(debs[0]), open(debs[0], "rb"))})
        assert response.status_code == 200, f"Error with request {response}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-origin', type=str, default='deb.fbn.org', help='Debian Repo hostname')
    parser.add_argument('cran_pkg_name', type=str, nargs=1, help='package to build.  ex: ggplot2')

    app_args = parser.parse_args()
    app_args.cran_pkg_name = app_args.cran_pkg_name[0]

    os.environ['MAKE'] = f"make -j {_num_cpus}"

    if not os.path.exists(_dist_path):
        with open(_dist_path, "w") as f:
            f.write(_dist_template.format(origin=app_args.origin))

    # Install dependencies
    deps = _get_dependencies(app_args.cran_pkg_name)
    _install_r_deps(deps)

    # Build source package
    print("Building source package")
    subprocess.check_call(["cran2deb", "build", app_args.cran_pkg_name])

    # Build deb package
    _build_pkg_dsc(app_args.cran_pkg_name)


if __name__ == '__main__':
    main()
