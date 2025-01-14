import re
import hashlib
import logging
from .db import db_license_override_name, db_license_override_hash
from .util import chomp


_logger = logging.getLogger(__name__)


def is_acceptable_license(license, verbose=False, debug=False):
    if verbose:
        print(f"is_acceptable_license: license: {license}")

    if re.search(r'^file ', license):
        _logger.info("The package has a file license. This needs individual checking and settings in the respective table (IGNORING).")
        return True

    license = license_text_reduce(license)
    if debug:
        print("**** a ****")

    action = db_license_override_name(license)
    if verbose:
        print("**** action: ****")
        print(action)

    if action is not None:
        if debug:
            print("**** c1 ****")
        return True

    if debug:
        print("**** c ****")

    license = license_text_further_reduce(license)
    if debug:
        print("**** d ****")

    action = db_license_override_name(license)
    if debug:
        print("**** e ****")

    if action is not None:
        _logger.warning(f'Accepting/rejecting wild license as {license}. FIX THE PACKAGE!')
        return action

    license = license_text_extreme_reduce(license)
    if debug:
        print("**** f ****")

    action = db_license_override_name(license)
    if debug:
        print("**** g ****")

    if action is not None:
        _logger.warning(f'Accepting/rejecting wild license as {license}. FIX THE PACKAGE!')
        return action

    _logger.error(f'is_acceptable_license: Wild license {license} did not match classic rules; rejecting.')
    return False


def license_text_reduce(license, verbose=False, debug=False):
    if verbose:
        print(f"license_text_reduce license: {license}")

    if isinstance(license, str):
        license = license.encode('latin1').decode('latin1')

    license = re.sub(r'\s+', ' ', license)
    license = license.lower()
    license = chomp(re.sub(r'\( ?[<=>!]+ ?[0-9.-]+ ?\)', '', re.sub(r'-[0-9.-]+', '', license)))
    license = chomp(re.sub(r'\s+', ' ', license))

    if debug:
        print(f"license_text_reduce: {license}")

    return license


def license_text_further_reduce(license, verbose=True):
    if verbose:
        print(f"license_text_further_reduce license: {license}")

    license = re.sub(r'http://www.gnu.org/[a-zA-Z0-9/._-]*', '', license)
    license = re.sub(r'http://www.x.org/[a-zA-Z0-9/._-]*', '', license)
    license = re.sub(r'http://www.opensource.org/[a-zA-Z0-9/._-]*', '', license)
    license = re.sub(r'[[:punct:]]+', '', license)
    license = chomp(re.sub(r'\s+', ' ', license))
    license = re.sub(r'the', '', license)
    license = re.sub(r'see', '', license)
    license = re.sub(r'standard', '', license)
    license = re.sub(r'licen[sc]e', '', license)
    license = re.sub(r'(gnu )?(gpl|general public)', 'gpl', license)
    license = re.sub(r'(mozilla )?(mpl|mozilla public)', 'mpl', license)
    license = chomp(re.sub(r'\s+', ' ', license))

    return license


def license_text_extreme_reduce(license, verbose=True):
    if verbose:
        print(f"license_text_extreme_reduce license: {license}")

    license = re.sub(r'(ver?sion|v)? *[0-9.-]+ *(or *(higher|later|newer|greater|above))?', '', license)
    license = chomp(re.sub(r'\s+', ' ', license))

    return license


def license_text_hash_reduce(text, verbose=True):
    if verbose:
        print(f"license_text_hash_reduce text: {text}")

    return chomp(text.lower().replace(r'\s+', ' '))


def get_license(pkg, license, verbose=False):
    license = re.sub(r'\s+$', ' ', license)

    if re.search(r'^file\s', license):
        _logger.info("License recognised as 'file'-based license.")

        if re.search(r'^file\s+LICEN[CS]E$', license):
            file = re.sub(r'file\s+', '', license)
            path = os.path.join(pkg['path'], file)

            if os.path.exists(path):
                with open(path, 'r') as f:
                    license = f.read()
            else:
                path = os.path.join(pkg['path'], 'inst', file)
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        license = f.read()
                else:
                    _logger.error(f"Could not locate license file expected at '{path}' or at '{os.path.join(pkg['path'], file)}'.")
                    return None
        else:
            _logger.error(f"Invalid license file specification, expected 'LICENSE' as filename, got: {license}")
            return None

    return license


def get_license_hash(pkg, license, verbose=False):
    license_text = get_license(pkg, license, verbose=verbose)
    if license_text is None:
        return None
    return hashlib.sha1(license_text.encode('utf-8')).hexdigest()


def is_acceptable_hash_license(pkg, license, verbose=True, debug=True):
    if debug:
        print(f"is_acceptable_hash_license: pkg['name']={pkg['name']}, license={license}")

    license_sha1 = get_license_hash(pkg, license, verbose=verbose)
    if license_sha1 is None:
        if verbose:
            print("is_acceptable_hash_license: get_license_hash(pkg, license) returned None, returning False.")
        return False
    elif verbose:
        _logger.info(f"is_acceptable_hash_license, license_sha1 determined: {license_sha1}")

    action = db_license_override_hash(license_sha1)
    if action is None:
        if verbose:
            print("is_acceptable_hash_license: db_license_override_hash(license_sha1) returned None, returning False.")
        action = False
    elif len(action) == 0:
        _logger.info("An error occurred, len(action) == 0, ignoring package.")
        action = False
    elif action is None:
        _logger.info("An error occurred, action is None, ignoring package.")
        action = False

    if action:
        _logger.warning(f'Wild license {license} accepted via hash {license_sha1}')

    return action


def accept_license(pkg, verbose=True):
    if 'License' not in pkg['description']:
        _logger.error('Package has no License: field in description!')
        return None

    accept = None
    if verbose:
        print(f"accept_license: pkg: {pkg['srcname']}")

    license = pkg['description']['License']
    if verbose:
        print(f"                license: {license}")

    for l in chomp(license).split('|'):
        if verbose:
            print(f"Investigating: {l}")

        if is_acceptable_license(l):
            accept = l
            break
        elif is_acceptable_hash_license(pkg, l, verbose=verbose):
            accept = l
            break
        else:
            _logger.info(f"Could not accept license {l} for package {pkg}")

    if accept is None:
        _logger.error(f'No acceptable license: {pkg["description"]["License"]}')
    else:
        _logger.info(f'Auto-accepted license {accept}')

    if accept == 'Unlimited':
        accept = 'Unlimited (no restrictions on distribution or use other than those imposed by relevant laws)'

    return accept
