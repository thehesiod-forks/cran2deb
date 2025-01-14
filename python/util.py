import subprocess
import re
import logging


_logger = logging.getLogger(__name__)


def iterate(xs, z, fun):
    y = z
    for x in xs:
        y = fun(y, x)
    return y

def chomp(x):
    # remove leading and trailing spaces
    return re.sub(r'^\s+|\s+$', '', x)

def host_arch():
    # return the host system architecture
    return subprocess.check_output(['dpkg-architecture', '-qDEB_HOST_ARCH']).decode().strip()

def err(*args):
    _logger.error(*args)
    exit()

def exit():
    import sys
    sys.exit()
