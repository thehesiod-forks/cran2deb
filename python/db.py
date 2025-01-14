import sqlite3
import os
import datetime

from .globals import cache_root, which_system, scm_revision
from .version import version_upstream


def db_start():
    con = sqlite3.connect(os.path.join(cache_root, 'cran2deb.db'))
    cur = con.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS sysreq_override (
                    depend_alias TEXT NOT NULL,
                    r_pattern TEXT PRIMARY KEY NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS debian_dependency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    alias TEXT NOT NULL,
                    build INTEGER NOT NULL,
                    debian_pkg TEXT NOT NULL,
                    UNIQUE (alias, build, debian_pkg))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS forced_depends (
                    r_name TEXT NOT NULL,
                    depend_alias TEXT NOT NULL,
                    PRIMARY KEY (r_name, depend_alias))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS license_override (
                    name TEXT PRIMARY KEY NOT NULL,
                    accept INT NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS license_hashes (
                    name TEXT NOT NULL,
                    sha1 TEXT PRIMARY KEY NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS database_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    version_date INTEGER NOT NULL,
                    base_epoch INTEGER NOT NULL)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS packages (
                    package TEXT PRIMARY KEY NOT NULL,
                    latest_r_version TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS builds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    system TEXT NOT NULL,
                    package TEXT NOT NULL,
                    r_version TEXT NOT NULL,
                    deb_epoch INTEGER NOT NULL,
                    deb_revision INTEGER NOT NULL,
                    db_version INTEGER NOT NULL,
                    date_stamp TEXT NOT NULL,
                    time_stamp TEXT NOT NULL,
                    scm_revision TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    log TEXT,
                    UNIQUE(package, system, r_version, deb_epoch, deb_revision, db_version))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS blacklist_packages (
                    package TEXT PRIMARY KEY NOT NULL,
                    nonfree INTEGER NOT NULL DEFAULT 0,
                    obsolete INTEGER NOT NULL DEFAULT 0,
                    broken_dependency INTEGER NOT NULL DEFAULT 0,
                    unsatisfied_dependency INTEGER NOT NULL DEFAULT 0,
                    breaks_cran2deb INTEGER NOT NULL DEFAULT 0,
                    other INTEGER NOT NULL DEFAULT 0,
                    explanation TEXT NOT NULL)''')
    con.commit()
    return con

def db_stop(con, bump=False):
    if bump:
        db_bump(con)
    con.close()

def db_quote(text):
    return f"""'{text.replace("'", "''")}'"""

def db_now():
    return int(datetime.datetime.now().strftime('%Y%m%d'))

def db_cur_version(con):
    cur = con.cursor()
    cur.execute('SELECT max(version) FROM database_versions')
    return cur.fetchone()[0]

def db_base_epoch(con):
    cur = con.cursor()
    cur.execute('''SELECT max(base_epoch) FROM database_versions
                   WHERE version IN (SELECT max(version) FROM database_versions)''')
    return cur.fetchone()[0]

def db_get_base_epoch():
    con = db_start()
    v = db_base_epoch(con)
    db_stop(con)
    return v

def db_get_version():
    con = db_start()
    v = db_cur_version(con)
    db_stop(con)
    return v

def db_add_version(con, version, epoch):
    cur = con.cursor()
    cur.execute('''INSERT INTO database_versions (version, version_date, base_epoch)
                   VALUES (?, ?, ?)''', (version, db_now(), epoch))
    con.commit()

def db_bump(con):
    db_add_version(con, db_cur_version(con) + 1, db_base_epoch(con))

def db_bump_epoch(con):
    db_add_version(con, db_cur_version(con) + 1, db_base_epoch(con) + 1)

def db_sysreq_override(sysreq_text):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT DISTINCT depend_alias FROM sysreq_override WHERE
                   ? LIKE r_pattern''', (sysreq_text.lower(),))
    results = cur.fetchall()
    db_stop(con)
    if len(results) == 0:
        return None
    return results[0][0]

def db_add_sysreq_override(pattern, depend_alias):
    con = db_start()
    cur = con.cursor()
    cur.execute('''INSERT OR REPLACE INTO sysreq_override (depend_alias, r_pattern)
                   VALUES (?, ?)''', (depend_alias.lower(), pattern.lower()))
    con.commit()
    db_stop(con)

def db_sysreq_overrides():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT * FROM sysreq_override')
    overrides = cur.fetchall()
    db_stop(con)
    return overrides

def db_get_depends(depend_alias, build=False):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT DISTINCT debian_pkg FROM debian_dependency WHERE
                   ? = alias AND ? = build''', (depend_alias.lower(), int(build)))
    results = cur.fetchall()
    db_stop(con)
    return [row[0] for row in results]

def db_add_depends(depend_alias, debian_pkg, build=False):
    con = db_start()
    cur = con.cursor()
    cur.execute('''INSERT OR REPLACE INTO debian_dependency (alias, build, debian_pkg)
                   VALUES (?, ?, ?)''', (depend_alias.lower(), int(build), debian_pkg.lower()))
    con.commit()
    db_stop(con)

def db_wipe_depends(depend_alias, debian_pkg, alias):
    con = db_start()
    cur = con.cursor()
    cur.execute('''DELETE FROM sysreq_override WHERE depend_alias LIKE ?''', (depend_alias.lower(),))
    cur.execute('''DELETE FROM debian_dependency WHERE debian_pkg LIKE ? OR alias LIKE ?''', (debian_pkg.lower(), alias.lower()))
    con.commit()
    db_stop(con)

def db_depends():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT * FROM debian_dependency')
    depends = cur.fetchall()
    db_stop(con)
    return depends

def db_get_forced_depends(r_name):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT depend_alias FROM forced_depends WHERE ? = r_name''', (r_name,))
    forced_depends = cur.fetchall()
    db_stop(con)
    return [row[0] for row in forced_depends]

def db_add_forced_depends(r_name, depend_alias):
    if not db_get_depends(depend_alias, build=False) and not db_get_depends(depend_alias, build=True):
        raise Exception(f'Debian dependency alias {depend_alias} is not known, yet trying to force a dependency on it?')
    con = db_start()
    cur = con.cursor()
    cur.execute('''INSERT OR REPLACE INTO forced_depends (r_name, depend_alias)
                   VALUES (?, ?)''', (r_name, depend_alias))
    con.commit()
    db_stop(con)

def db_forced_depends():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT * FROM forced_depends')
    depends = cur.fetchall()
    db_stop(con)
    return depends

def db_license_override_name(name):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT DISTINCT accept FROM license_override WHERE ? = name''', (name.lower(),))
    results = cur.fetchall()
    db_stop(con)
    if len(results) == 0:
        return None
    return bool(results[0][0])

def db_add_license_override(name, accept):
    if accept not in [True, False]:
        raise Exception('accept must be TRUE or FALSE')
    con = db_start()
    cur = con.cursor()
    cur.execute('''INSERT OR REPLACE INTO license_override (name, accept)
                   VALUES (?, ?)''', (name.lower(), int(accept)))
    con.commit()
    db_stop(con)

def db_license_override_hash(license_sha1):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT DISTINCT accept FROM license_override
                   INNER JOIN license_hashes ON license_hashes.name = license_override.name
                   WHERE ? = license_hashes.sha1''', (license_sha1.lower(),))
    results = cur.fetchall()
    db_stop(con)
    if len(results) == 0:
        return None
    return bool(results[0][0])

def db_license_overrides():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT * FROM license_override')
    overrides = cur.fetchall()
    cur.execute('SELECT * FROM license_hashes')
    hashes = cur.fetchall()
    db_stop(con)
    return {'overrides': overrides, 'hashes': hashes}

def db_add_license_hash(name, license_sha1):
    if db_license_override_name(name) is None:
        raise Exception(f'license {name} is not known, yet trying to add a hash for it?')
    con = db_start()
    cur = con.cursor()
    cur.execute('''INSERT OR REPLACE INTO license_hashes (name, sha1)
                   VALUES (?, ?)''', (name.lower(), license_sha1.lower()))
    con.commit()
    db_stop(con)

def db_update_package_versions(available):
    con = db_start()
    cur = con.cursor()
    cur.execute('DROP TABLE IF EXISTS packages')
    con.commit()
    cur.execute('''CREATE TABLE IF NOT EXISTS packages (
                    package TEXT PRIMARY KEY NOT NULL,
                    latest_r_version TEXT)''')
    for package in available['Package']:
        cur.execute('''INSERT OR REPLACE INTO packages (package, latest_r_version)
                       VALUES (?, ?)''', (package, available.loc[package, 'Version']))
    cur.execute('''DELETE FROM builds WHERE builds.package NOT IN (SELECT package FROM packages)''')
    con.commit()
    db_stop(con)

db_date_format = '%Y-%m-%d'
db_time_format = '%H:%M:%S.%f'

def db_record_build(package, deb_version, log, success=False):
    log = '\n'.join(log)
    end = len(log)
    max_log_len = 10240
    if end > max_log_len:
        log = db_quote(log[end - max_log_len:])
    else:
        log = db_quote(log)
    con = db_start()
    cur = con.cursor()
    sqlcmd = f'''INSERT OR REPLACE INTO builds
                 (package, system, r_version, deb_epoch, deb_revision, db_version, success, date_stamp, time_stamp, scm_revision, log)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    cur.execute(sqlcmd, (package, which_system, version_upstream(deb_version), version_epoch(deb_version),
                         version_revision(deb_version), db_cur_version(con), int(success),
                         datetime.datetime.now().strftime(db_date_format),
                         datetime.datetime.now().strftime(db_time_format), scm_revision, log))
    con.commit()
    db_stop(con)

def db_builds(pkgname):
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT * FROM builds WHERE success = 1 AND system = ? AND package = ?''', (which_system, pkgname))
    build = cur.fetchall()
    db_stop(con)
    if len(build) == 0:
        return None
    return db_cleanup_builds(build)

def db_cleanup_builds(build, verbose=False):
    build = [dict(row) for row in build]
    for b in build:
        b['success'] = bool(b['success'])
        dt = datetime.datetime.strptime(f"{b['date_stamp']} {b['time_stamp']}", f"{db_date_format} {db_time_format}")
        b['date_stamp'] = dt
        del b['time_stamp']
    if verbose:
        print("db_cleanup_builds: newdf")
        print(build)
    return build

def db_latest_build(pkgname, verbose=False, debug=False):
    if verbose:
        print(f"db_latest_build: pkgname: {pkgname}")
    con = db_start()
    if debug:
        print("       connection was opened")
    cur = con.cursor()
    cur.execute('''SELECT * FROM builds
                   NATURAL JOIN (SELECT package, max(id) AS max_id FROM builds
                                 WHERE system = ? GROUP BY package) AS last
                   WHERE id = max_id AND builds.package = ?''', (which_system, pkgname))
    build = cur.fetchall()
    if debug:
        print("       dbGetQuery was executed:")
        print("       print(build):")
        print(build)
    db_stop(con)
    if debug:
        print("       connection was closed")
    if len(build) == 0 or len(build) == 0:
        return None
    return db_cleanup_builds(build)

def db_latest_build_version(pkgname, verbose=False):
    if verbose:
        print(f"db_latest_build_version: pkgname: {pkgname}")
    build = db_latest_build(pkgname)
    if build is None or len(build) == 0:
        return None
    return version_new(build[0]['r_version'], build[0]['deb_revision'], build[0]['deb_epoch'])

def db_latest_build_status(pkgname, verbose=False):
    if verbose:
        print(f"db_latest_build_status: pkgname: {pkgname}")
    build = db_latest_build(pkgname)
    if build is None or len(build) == 0:
        return None
    return [build[0]['success'], build[0]['log']]

def db_outdated_packages():
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT packages.package FROM packages
                   LEFT OUTER JOIN (
                       SELECT * FROM builds
                       NATURAL JOIN (SELECT package, max(id) AS max_id FROM builds
                                     WHERE system = ? GROUP BY package) AS last
                       WHERE id = max_id) AS build
                   ON build.package = packages.package
                   WHERE build.package IS NULL
                   OR build.db_version < (SELECT max(version) FROM database_versions)
                   OR build.deb_epoch < (SELECT max(base_epoch) FROM database_versions
                                         WHERE version IN (SELECT max(version) FROM database_versions))
                   OR build.r_version != packages.latest_r_version''', (which_system,))
    packages = cur.fetchall()
    db_stop(con)
    return [row[0] for row in packages]

def db_blacklist_packages():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT package FROM blacklist_packages')
    packages = cur.fetchall()
    db_stop(con)
    return [row[0] for row in packages]

def db_blacklist_reasons():
    con = db_start()
    cur = con.cursor()
    cur.execute('SELECT package, explanation FROM blacklist_packages GROUP BY explanation')
    packages = cur.fetchall()
    db_stop(con)
    return packages

def db_todays_builds():
    today = datetime.datetime.now().strftime(db_date_format)
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT id, success, system, package, r_version AS version, deb_epoch AS epo,
                          deb_revision AS rev, scm_revision AS svnrev, db_version AS db, date_stamp, time_stamp
                   FROM builds WHERE date_stamp = ?''', (today,))
    builds = cur.fetchall()
    db_stop(con)
    return builds

def db_successful_builds():
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT system, package, r_version, date_stamp, time_stamp
                   FROM builds NATURAL JOIN (SELECT system, package, max(id) AS id
                                             FROM builds
                                             WHERE package NOT IN (SELECT package FROM blacklist_packages)
                                             GROUP BY package, system)
                   WHERE success = 1''')
    builds = cur.fetchall()
    db_stop(con)
    return builds

def db_failed_builds():
    con = db_start()
    cur = con.cursor()
    cur.execute('''SELECT system, package, r_version, date_stamp, time_stamp
                   FROM builds NATURAL JOIN (SELECT system, package, max(id) AS id
                                             FROM builds
                                             WHERE package NOT IN (SELECT package FROM blacklist_packages)
                                             GROUP BY package, system)
                   WHERE success = 0''')
    builds = cur.fetchall()
    db_stop(con)
    return builds
