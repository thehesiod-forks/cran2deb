import re
import pandas as pd


def r_requiring(names, available, r_depend_fields):
    # approximately prune first into a smaller availability
    candidates = [name for name in available.index if any(re.search('|'.join(names), available.loc[name, field]) for field in r_depend_fields)]
    if len(candidates) == 0:
        return []

    # find a logical index into available of every package
    # whose dependency field contains at least one element of names.
    prereq = []
    def dep_matches(dep):
        return chomp(re.sub(r'\([^\\)]+\)', '', dep)) in names

    def any_dep_matches(name, field=None):
        return any(dep_matches(dep) for dep in re.split(r'[[:space:]]*,[[:space:]]*', chomp(available.loc[name, field])))

    for field in r_depend_fields:
        matches = [name for name in candidates if any_dep_matches(name, field)]
        if len(matches) > 0:
            prereq.extend(matches)

    return list(set(prereq))


def r_dependencies_of(name=None, description=None, available=None, r_depend_fields=None, base_pkgs=None):
    # find the immediate dependencies (children in the dependency graph) of an R package
    if name is not None and (name == 'R' or name in base_pkgs):
        return pd.DataFrame()

    if description is None and name is None:
        raise ValueError('must specify either a description or a name.')

    if description is None:
        if name not in available.index:
            # unavailable packages don't depend upon anything
            return pd.DataFrame()
        description = pd.DataFrame()
        # keep only the interesting fields
        for field in r_depend_fields:
            if field not in available.columns:
                continue
            description.loc[0, field] = available.loc[name, field]

    # extract the dependencies from the description
    deps = pd.DataFrame()
    for field in r_depend_fields:
        if field not in description.columns:
            continue
        new_deps = [r_parse_dep_field(dep) for dep in re.split(r'[[:space:]]*,[[:space:]]*', chomp(description.loc[0, field]))]
        deps = pd.concat([deps, pd.DataFrame(new_deps).dropna()])

    return deps


def r_parse_dep_field(dep):
    if dep is None:
        return None

    # remove other comments
    dep = re.sub(r'(\\(\\)|\\([[:space:]]*[^<=>!].*\\))', '', dep)
    # squish spaces
    dep = chomp(re.sub(r'[[:space:]]+', ' ', dep))
    # parse version
    pat = r'^([^ ()]+) ?(\\( ?([<=>!]+ ?[0-9.-]+) ?\\))?$'
    if not re.search(pat, dep):
        raise ValueError(f'R dependency {dep} does not appear to be well-formed')
    version = re.sub(pat, r'\\3', dep)
    dep = re.sub(pat, r'\\1', dep)
    return {'name': dep, 'version': version}


def r_dependency_closure(fringe, available, r_depend_fields, base_pkgs, forward_arcs=True):
    # find the transitive closure of the dependencies/prerequisites of some R packages
    closure = []
    if isinstance(fringe, pd.DataFrame):
        fringe = fringe['name'].tolist()

    def fun(x):
        return r_dependencies_of(name=x, available=available, r_depend_fields=r_depend_fields, base_pkgs=base_pkgs)['name'].tolist()

    if not forward_arcs:
        fun = lambda x: r_requiring([x], available, r_depend_fields)

    while len(fringe) > 0:
        # pop off the top
        top = fringe.pop(0)
        src = pkgname_as_debian(top, binary=False)
        if src == 'R':
            continue
        newdeps = fun(top)
        closure.append(top)
        fringe.extend(newdeps)

    # build order
    return list(reversed(list(dict.fromkeys(closure))))


def chomp(x):
    # remove leading and trailing spaces
    return re.sub(r'^\s+|\s+$', '', x)

def pkgname_as_debian(name, binary=True):
    # Placeholder function for pkgname_as_debian
    return name.lower()
