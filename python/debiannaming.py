import re


def repourl_as_debian(url):
    # map the url to a repository onto its name in debian package naming
    if re.search('cran', url):
        return 'cran'
    elif re.search('bioc', url):
        return 'bioc'
    elif re.search('omegahat', url):
        return 'omegahat'
    elif re.search('rforge', url):
        return 'rforge'
    else:
        raise ValueError(f'unknown repository: {url}')


def pkgname_as_debian(name, repopref=None, version=None, binary=True, build=False, base_pkgs=None, available=None):
    # generate the debian package name corresponding to the R package name
    if base_pkgs is None:
        base_pkgs = []
    if available is None:
        available = {}

    if name in base_pkgs:
        name = 'R'
    if name == 'R':
        # R is special.
        if binary:
            if build:
                debname = 'r-base-dev'
            else:
                debname = 'r-base-core'
        else:
            debname = 'R'
    else:
        # XXX: data.frame rownames are unique, so always override repopref for now.
        debname = name.lower()
        if binary:
            if name.lower() in available:
                repopref = repourl_as_debian(available[name.lower()]['Repository']).lower()
            elif repopref is None:
                repopref = 'unknown'
            debname = f'r-{repopref}-{debname}'

    if version is not None and len(version) > 1:
        debname = f'{debname} ({version})'

    return debname
