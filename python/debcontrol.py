import logging
import os
import re

from .debiannaming import pkgname_as_debian
from .db import db_sysreq_override, db_get_forced_depends, db_get_depends
from .util import chomp
from .rdep import r_dependencies_of, r_dependency_closure
from .globals import patch_dir, maintainer_c2d


_logger = logging.getLogger(__name__)


def get_dependencies(pkg, extra_deps, verbose=True):
    # determine dependencies
    dependencies = r_dependencies_of(description=pkg['description'])
    depends = {}

    # these are used for generating the Depends fields
    def as_deb(r, build):
        return pkgname_as_debian(
            dependencies.loc[r, 'name'],
            version=dependencies.loc[r, 'version'],
            repopref=pkg['repo'],
            build=build
        )

    depends['bin'] = [as_deb(r, False) for r in dependencies.index]
    depends['build'] = [as_deb(r, True) for r in dependencies.index]

    # add the command line dependencies
    depends['bin'] = extra_deps['deb'] + depends['bin']
    depends['build'] = extra_deps['deb'] + depends['build']

    # add the system requirements
    if 'SystemRequirements' in pkg['description'].columns:
        sysreq = sysreqs_as_debian(pkg['description'].loc[0, 'SystemRequirements'], verbose=verbose)
        if sysreq and isinstance(sysreq, dict):
            depends['bin'] = sysreq['bin'] + depends['bin']
            depends['build'] = sysreq['build'] + depends['build']
        else:
            if sysreq is None:
                _logger.info('Houston, we have a NULL sysreq')
            else:
                if verbose:
                    print("sysreq:", sysreq)
                raise Exception('Cannot interpret system dependency, fix package.\n')

    forced = forced_deps_as_debian(pkg['name'])
    if forced:
        _logger.info('forced build dependencies:', ', '.join(forced['build']))
        _logger.info('forced binary dependencies:', ', '.join(forced['bin']))
        depends['bin'] = forced['bin'] + depends['bin']
        depends['build'] = forced['build'] + depends['build']

    # make sure we depend upon R in some way...
    if not any(re.match('^r-base', dep) for dep in depends['build']):
        depends['build'].append(pkgname_as_debian('R', version='>= 2.7.0', build=True))
        depends['bin'].append(pkgname_as_debian('R', version='>= 2.7.0', build=False))

    # also include stuff to allow tcltk to build (suggested by Dirk)
    depends['build'].extend(['xvfb', 'xauth', 'xfonts-base'])

    # make all bin dependencies build dependencies.
    depends['build'].extend(depends['bin'])

    # remove duplicates
    depends = {k: list(set(v)) for k, v in depends.items()}

    # append the Debian dependencies
    depends['build'].extend(['debhelper (>> 4.1.0)', 'cdbs'])
    if os.path.exists(os.path.join(patch_dir, pkg['name'])):
        depends['build'].append('dpatch')
    if pkg['archdep']:
        depends['bin'].append('${shlibs:Depends}')

    # the names of dependent source packages (to find the .changes file to
    # upload via dput). these can be found recursively.
    depends['r'] = r_dependency_closure(dependencies)
    # append command line dependencies
    depends['r'].extend(extra_deps['r'])

    return depends

def sysreqs_as_debian(sysreq_text, verbose=False):
    # form of this field is unspecified (ugh) but most people seem to stick
    # with this
    aliases = []

    # drop notes
    sysreq_text = re.sub(r'[Nn][Oo][Tt][Ee]:\s.*', '', sysreq_text)

    # conversion from and to commata and lower case
    sysreq_text = re.sub(r'\s+and\s+', ' , ', sysreq_text.lower())

    for sysreq in re.split(r'\s*,\s*', sysreq_text):
        sysreq = re.sub(r'[\r\n]', '', sysreq).strip()

        if verbose:
            print(f"sysreq to investigate: '{sysreq}'.")

        startreq = sysreq

        # constant case (redundant)
        sysreq = sysreq.lower()

        # first try pre-sub'd string, as below breaks ICU4C
        alias = db_sysreq_override(sysreq)

        if not alias:
            _logger.info(f"do not know what to do with SystemRequirement:'{sysreq}' attempting substitutions")

            # drop version information/comments for now
            sysreq = re.sub(r'\[[^)]*\]', '', sysreq)
            sysreq = re.sub(r'\([^)]*\)', '', sysreq)
            sysreq = re.sub(r'\[[^)]*\]', '', sysreq)
            sysreq = re.sub(r'version', '', sysreq)
            sysreq = re.sub(r'from', '', sysreq)
            sysreq = re.sub(r'[<>=]*\s*\d+[\d.:~-]*', '', sysreq)

            # byebye URLs
            sysreq = re.sub(r'(ht|f)tps?://[\w!?*"\'(),%$_@.&+/=-]*', '', sysreq)

            # squish out space
            sysreq = chomp(re.sub(r'\s+', ' ', sysreq))

            # no final dot and neither final blanks
            sysreq = re.sub(r'\.?$', '', sysreq).strip()

            if not sysreq:
                _logger.info('part of the SystemRequirement became nothing')
                continue

            alias = db_sysreq_override(sysreq)

        if not alias:
            _logger.error(f"do not know what to do with SystemRequirement:'{sysreq}'")
            _logger.error(f'original SystemRequirement: {startreq}')
            raise Exception('unmet system requirement')

        _logger.info(f"mapped SystemRequirement '{startreq}' onto '{alias}' via '{sysreq}'.")
        aliases.append(alias)

    return map_aliases_to_debian(aliases)

def forced_deps_as_debian(r_name):
    aliases = db_get_forced_depends(r_name)
    return map_aliases_to_debian(aliases)

def map_aliases_to_debian(aliases):
    if not aliases:
        return aliases
    debs = {}
    debs['bin'] = [dep for alias in aliases for dep in db_get_depends(alias)]
    debs['build'] = [dep for alias in aliases for dep in db_get_depends(alias, build=True)]
    debs['bin'] = [dep for dep in debs['bin'] if dep != 'build-essential']
    debs['build'] = [dep for dep in debs['build'] if dep != 'build-essential']
    return debs

def generate_control(pkg):
    # construct control file
    control = {}

    control['Source'] = pkg['srcname']
    control['Section'] = 'gnu-r'
    control['Priority'] = 'optional'
    control['Maintainer'] = maintainer_c2d
    control['Build-Depends'] = ', '.join(pkg['depends']['build'])
    control['Standards-Version'] = '4.1.3'
    if 'URL' in pkg['description'].columns:
        control['Homepage'] = pkg['description'].loc[0, 'URL']

    control['Package'] = pkg['debname']
    control['Architecture'] = 'all' if not pkg['archdep'] else 'any'
    control['Depends'] = "${misc:Depends}, " + ', '.join(pkg['depends']['bin'])

    # generate the description
    descr = 'GNU R package "'
    if 'Title' in pkg['description'].columns:
        descr += pkg['description'].loc[0, 'Title']
    else:
        descr += pkg['name']
    long_descr = pkg['description'].loc[0, 'Description']

    if not long_descr:
        # bypass lintian extended-description-is-empty for which we care not.
        long_descr = 'The author/maintainer of this package did not care to enter a longer description.'

    # using \n\n.\n\n is not very nice, but is necessary to make sure
    # the longer description does not begin on the synopsis line --- R's
    # write.dcf does not appear to have a nicer way of doing this.
    descr += f'"\n\n{long_descr}'
    # add some extra nice info about the original R package
    for r_info in ['Author', 'Maintainer']:
        if r_info in pkg['description'].columns:
            descr += f'\n\n{r_info}: {pkg["description"].loc[0, r_info]}'
    if descr.encoding == "unknown":
        descr.encoding = "latin1"  # or should it be UTF-8

    control['Description'] = descr

    # Debian policy says 72 char width; indent minimally
    with open(pkg['debfile']('control.in'), 'w') as f:
        f.write('\n'.join([f'{k}: {v}' for k, v in control.items()]))
