import os
import shutil
import subprocess
import datetime

from .version import version_upstream, version_epoch, version_revision, version_new, new_build_version
from .globals import which_system, scm_revision, maintainer_c2d
from .db import db_get_version, db_builds
from .util import host_arch
from .license import accept_license
from .debcontrol import get_dependencies, generate_control
from .patch import apply_patches
from .lintian import generate_lintian


def append_build_from_pkg(pkg, builds):
    pkg_build = {
        'id': -1,  # never used
        'package': pkg['name'],
        'system': which_system,
        'r_version': version_upstream(pkg['debversion']),
        'deb_epoch': version_epoch(pkg['debversion']),
        'deb_revision': version_revision(pkg['debversion']),
        'db_version': db_get_version(),
        'date_stamp': pkg['date_stamp'],
        'scm_revision': scm_revision,
        'success': 1,  # never used
        'log': ''  # never used
    }
    builds.append(pkg_build)
    return builds

def generate_changelog(pkg):
    builds = append_build_from_pkg(pkg, db_builds(pkg['name']))
    for b in reversed(builds):
        generate_changelog_entry(b, pkg['debfile']('changelog.in'))

def generate_changelog_entry(build, changelog):
    debversion = version_new(build['r_version'], build['deb_revision'], build['deb_epoch'])
    dist = os.getenv("DIST", "testing")
    with open(changelog, 'a') as f:
        f.write(f"{build['srcname']} ({debversion}) {dist}; urgency=low\n\n")
        f.write(f"  * cran2deb {build['scm_revision']} with DB version {int(build['db_version'])}.\n\n")
        f.write(f" -- {maintainer_c2d}  {build['date_stamp'].strftime('%a, %d %b %Y %H:%M:%S %z')}\n\n\n")

def generate_rules(pkg):
    with open(pkg['debfile']('rules'), 'w') as f:
        f.write('#!/usr/bin/make -f\n')
        f.write(f"debRreposname := {pkg['repo']}\n")
        f.write('include /usr/share/R/debian/r-cran.mk\n\n')
    if pkg['name'] in ["Rmpi", "npRmpi", "doMPI"]:
        with open(pkg['debfile']('rules'), 'a') as f:
            f.write("extraInstallFlags=\"--no-test-load\"\n")
    os.chmod(pkg['debfile']('rules'), 0o755)

def generate_compat(pkg):
    with open(pkg['debfile']('compat'), 'w') as f:
        f.write('9\n')
    os.chmod(pkg['debfile']('compat'), 0o664)

def generate_copyright(pkg):
    maintainer = pkg['description'].get('Maintainer', pkg['description'].get('Author', None))
    if not maintainer:
        raise ValueError('Maintainer and Author not defined in R DESCRIPTION')
    author = pkg['description'].get('Author', maintainer)
    with open(pkg['debfile']('copyright.in'), 'w') as f:
        f.write(f"This Debian package of the GNU R package {pkg['name']} was generated automatically using cran2deb4ubuntu by {maintainer_c2d}.\n\n")
        f.write(f"The original GNU R package is Copyright (C) {datetime.datetime.now().year} {author} and possibly others.\n\n")
        f.write(f"The original GNU R package is maintained by {maintainer} and was obtained from:\n\n")
        f.write(f"{pkg['repoURL']}\n\n\n")
        f.write(f"The GNU R package DESCRIPTION offers a Copyright licenses under the terms of the license: {pkg['license']}. On a Debian GNU/Linux system, common licenses are included in the directory /usr/share/common-licenses/.\n\n")
        f.write(f"The DESCRIPTION file for the original GNU R package can be found in /usr/lib/R/site-library/{pkg['debname']}/DESCRIPTION\n")

def prepare_new_debian(pkg, extra_deps):
    pkg['debversion'] = new_build_version(pkg['name'])
    debdir = os.path.join(pkg['path'], 'debian')
    pkg['debfile'] = lambda x: os.path.join(debdir, x)
    if os.path.exists(debdir):
        shutil.rmtree(debdir)
    os.makedirs(debdir)
    pkg['archdep'] = os.path.exists(os.path.join(pkg['path'], 'src'))
    pkg['arch'] = 'all' if not pkg['archdep'] else host_arch()
    pkg['license'] = accept_license(pkg)
    pkg['depends'] = get_dependencies(pkg, extra_deps)
    apply_patches(pkg)
    generate_lintian(pkg)
    generate_changelog(pkg)
    generate_rules(pkg)
    generate_copyright(pkg)
    generate_control(pkg)
    generate_compat(pkg)
    for file in ['control', 'changelog', 'copyright']:
        subprocess.run(['iconv', '-o', pkg['debfile'](file), '-t', 'utf8', '-c', pkg['debfile'](f"{file}.in")])
        os.remove(pkg['debfile'](f"{file}.in"))
    return pkg
