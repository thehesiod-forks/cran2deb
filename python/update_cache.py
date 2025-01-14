#!/usr/bin/env python3
import os
import subprocess
import pandas as pd
from .globals import cache_root
from .db import db_update_package_versions

from rpy2.robjects.packages import importr

import rpy2.robjects as ro
from rpy2.robjects import pandas2ri, default_converter
from rpy2.robjects.conversion import localconverter
import rpy2.robjects.conversion as cv
from rpy2 import rinterface_lib

pandas2ri.activate()

utils = importr('utils')
base = importr('base')
ctv = importr('ctv')

# import pydevd_pycharm
# pydevd_pycharm.settrace('host.docker.internal', port=55558, stdoutToServer=True, stderrToServer=True, suspend=False)

cache_file = os.path.join(cache_root, 'cache.pkl')

def _none2null(none_obj):
    return ro.r("NULL")


def update_cache():
    none_converter = cv.Converter("None converter")
    none_converter.py2rpy.register(type(None), _none2null)

    available = ro.r("NULL")
    ctv_available = ro.r("NULL")

    mirrors = {
        'BioC': 'http://www.bioconductor.org/packages/3.5',
        'CRAN': 'http://cran.r-project.org'
    }

    verbose = True
    debug = True

    with localconverter(pandas2ri.converter + ro.default_converter):
        for mirror_name, mirror_url in mirrors.items():
            print(f"Updating list of available R packages from {mirror_name} [{mirror_url}].")
            packages_retrieved = ro.r("NULL")

            # with (ro.default_converter + pandas2ri.converter).context():
            if mirror_name == "CRAN":
                packages_retrieved = utils.available_packages(utils.contrib_url(mirror_url))
            elif mirror_name == "BioC":
                repos = ["bioc", "data/annotation", "data/experiment", "extra"]
                for s in repos:
                    packages_retrieved = base.rbind(packages_retrieved, utils.available_packages(utils.contrib_url(f"{mirror_url}/{s}")))
            else:
                packages_retrieved = utils.available_packages(mirror_url)

            if verbose:
                print(f"Retrieved {len(packages_retrieved)} package descriptions.")
            available = base.rbind(available, packages_retrieved)

        if mirror_name == "CRAN":
            print('updating list of available R task views...')
            # TODO: figure out how to get dataframe of ctvlist
            # ctv_available = base.rbind(ctv_available, ctv.available_views(repo=mirror_url))
            ctv_available = None

        available: pd.DataFrame = ro.conversion.rpy2py(base.as_data_frame(available))
        available = available.applymap(lambda x: None if isinstance(x, rinterface_lib.sexp.NACharacterType) else x)
        dupes = available.index.duplicated()
        if dupes.sum() > 0:
            if verbose:
                print(f"Found {dupes.sum()} packages with the same name in different distributions. Those are now removed.")
            available = available[~dupes]
        else:
            if len(mirrors) > 1:
                print("All packages have different names.")

        print('updating list of base R packages...')
        inst_pkgs = ro.conversion.rpy2py(base.as_data_frame(utils.installed_packages(lib_loc="/usr/lib/R/library")))
        base_pkgs = inst_pkgs[inst_pkgs["Priority"] == 'base'].index.tolist()

    if any("E:" in pkg for pkg in base_pkgs):
        print("Cannot continue, the following error occurred:")
        print("\n".join(pkg for pkg in base_pkgs if "E:" in pkg))
        return

    if debug:
        print("The following base packages were determined:")
        print(base_pkgs)

    print('updating list of existing packages...')
    debian_pkgs = subprocess.check_output('apt-cache rdepends r-base-core | sed -e "/^  r-cran/{s/^[[:space:]]*r/r/;p}" -e d | sort -u', shell=True).decode().splitlines()

    if any("E:" in pkg for pkg in debian_pkgs):
        print("Cannot continue, the following error occurred:")
        print("\n".join(pkg for pkg in debian_pkgs if "E:" in pkg))
        return

    if verbose:
        print("The following packages were found to be available:")
        print(debian_pkgs)

    pd.to_pickle({'debian_pkgs': debian_pkgs, 'base_pkgs': base_pkgs, 'available': available, 'ctv_available': ctv_available}, cache_file)

    print('synchronising database...')
    db_update_package_versions(available)



if __name__ == "__main__":
    update_cache()
