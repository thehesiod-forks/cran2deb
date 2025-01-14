import re

version_suffix_step = 1
version_suffix = "cran"


def version_new(rver, debian_revision: int = 1, debian_epoch=None, verbose: bool = True):
    if verbose:
        print(f"rver: {rver} debian_revision: {debian_revision} debian_epoch: {debian_epoch}")

    if not re.match(r'^([0-9]+[.-])+[0-9]+$', rver):
        raise ValueError(f"Not a valid R package version: '{rver}'")

    if not re.match(r'^[0-9][A-Za-z0-9.+:~-]*$', rver):
        raise ValueError(f"R package version {rver} does not obviously translate into a valid Debian version.")

    if debian_epoch == 0 and ':' in rver:
        debian_epoch = 1

    if debian_epoch != 0:
        rver = f"{debian_epoch}:{rver}"

    return f"{rver}-{version_suffix_step}{version_suffix}{debian_revision}"

def version_epoch(pkgver):
    if ':' not in pkgver:
        return 0

    return int(re.sub(r'^([0-9]+):.*$', r'\1', pkgver))


def version_revision(pkgver: str):
    return int(re.sub(rf'.*-([0-9]+{version_suffix})?([0-9]+)$', r'\2', pkgver))


def version_upstream(pkgver: str, verbose: bool = False):
    if verbose:
        print(f"version_upstream: pkgver: {pkgver}")

    return re.sub(r'-[a-zA-Z0-9+.~]+$', '', re.sub(r'^[0-9]+:', '', pkgver))


def version_update(rver, prev_pkgver, prev_success, verbose=True):
    if verbose:
        print(f"version_update: rver: {rver} prev_pkgver: {prev_pkgver} prev_success: {prev_success}")

    prev_rver = version_upstream(prev_pkgver)
    if prev_rver == rver:
        inc = 1 if prev_success else 0
        return version_new(rver, debian_revision=version_revision(prev_pkgver) + inc, debian_epoch=version_epoch(prev_pkgver))

    return version_new(rver, debian_epoch=version_epoch(prev_pkgver))


def new_build_version(pkgname, available, db_latest_build_version, db_latest_build_status, verbose=False):
    print(f"new_build_version: pkgname: {pkgname}")

    if pkgname not in available:
        raise ValueError(f"tried to discover new version of {pkgname} but it does not appear to be available")

    db_ver = db_latest_build_version(pkgname)
    if verbose:
        print(f"db_ver: '{db_ver}'")

    db_succ = db_latest_build_status(pkgname)[0]
    if verbose:
        print(f"db_succ: '{db_succ}'")

    latest_r_ver = available[pkgname]['Version']
    if verbose:
        print(f"latest_r_ver: '{latest_r_ver}'")

    if db_ver is not None:
        return version_update(latest_r_ver, db_ver, db_succ)
