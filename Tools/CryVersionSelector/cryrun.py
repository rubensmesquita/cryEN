#!/usr/bin/env python3

import sys
import argparse
import os.path
import subprocess
import shutil
import glob

import configparser
import tempfile
import datetime
import zipfile
import stat

from win32com.shell import shell, shellcon
import win32file, win32api
import admin
import distutils.dir_util, distutils.file_util

import cryproject, cryregistry, crysolutiongenerator, release_project, cryrun_gui

#--- errors

def error_project_not_found (path):
    sys.stderr.write ("'%s' not found.\n" % path)
    sys.exit (600)

def error_project_json_decode (path):
    sys.stderr.write ("Unable to parse '%s'.\n" % path)
    sys.exit (601)

def error_unable_to_replace_file (path):
    sys.stderr.write ("Unable to replace file '%s'. Please remove the file manually.\n" % path)
    sys.exit (610)

def error_engine_tool_not_found (path):
    sys.stderr.write ("'%s' not found.\n" % path)
    sys.exit (620)

def error_cmake_not_found():
    sys.stderr.write ("Unable to locate CMake.\nPlease download and install CMake from https://cmake.org/download/ and make sure it is available through the PATH environment variable.\n")
    sys.exit (621)

def error_solution_not_found(path):
    sys.stderr.write ("Solution not found in '%s'. Make sure to first generate a solution if project contains code.\n" % path)
    sys.exit (641)

def print_subprocess (cmd):
    print (' '.join (map (lambda a: '"%s"' % a, cmd)))

#---

def get_cmake_exe_path():
    return os.path.join(get_cmake_dir(), 'Win32/bin/cmake.exe')

def get_cmake_dir():
    return os.path.join(get_engine_path(), 'Tools', 'CMake')

def get_tools_path():
    if getattr( sys, 'frozen', False ):
        ScriptPath = sys.executable
    else:
        ScriptPath = __file__

    return os.path.abspath (os.path.dirname (ScriptPath))

def get_engine_path():
    return os.path.abspath (os.path.join (get_tools_path(), '..', '..'))

def get_solution_dir (args):
    basename = os.path.basename (args.project_file)
    return os.path.join ('Solutions', "%s.%s" % (os.path.splitext (basename)[0], args.platform))

#-- BUILD ---

def cmd_build(args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    cmake_path = get_cmake_exe_path()
    if cmake_path is None:
        error_cmake_not_found()

    #--- cmake
    if cryproject.cmakelists_dir(project) is not None:
        project_path = os.path.dirname (os.path.abspath (args.project_file))
        solution_dir = get_solution_dir (args)

        subcmd = (
            cmake_path,
            '--build', solution_dir,
            '--config', args.config
        )

        print_subprocess (subcmd)
        errcode = subprocess.call(subcmd, cwd = project_path)
        if errcode != 0:
            sys.exit (errcode)

#--- PROJGEN ---

def cmd_cmake_gui(args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)
    dirname = os.path.dirname (os.path.abspath (args.project_file))

    #--- cpp
    cmakelists_dir = cryproject.cmakelists_dir(project)
    if cmakelists_dir is not None:
        cmake_path = get_cmake_exe_path()
        if cmake_path is None:
            error_cmake_not_found()

        project_path = os.path.abspath (os.path.dirname (args.project_file))
        solution_path = os.path.join (project_path, get_solution_dir (args))

        cmake_gui_path = cmake_path.replace('cmake.exe','cmake-gui.exe')

        subcmd = (cmake_gui_path)
        pid = subprocess.Popen([cmake_gui_path],cwd = solution_path)

def cmd_projgen(args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    project_path = os.path.abspath (os.path.dirname (args.project_file))
    engine_path = get_engine_path()

    cmakelists_dir = cryproject.cmakelists_dir(project)
    code_directory = os.path.join (project_path, cmakelists_dir)

    # Generate solutions
    crysolutiongenerator.generate_solution(args.project_file, code_directory, engine_path)

    cmakelists_path = os.path.join(os.path.join (project_path, cmakelists_dir), 'CMakeLists.txt')

    # Generate the Solution, skip on Crytek build agents
    if cmakelists_dir is not None and os.path.exists(cmakelists_path) and not args.buildmachine:

        cmake_dir = get_cmake_dir()
        cmake_path = get_cmake_exe_path()

        if cmake_path is None:
            error_cmake_not_found()

        # Run the GUI to select a config for CMake.
        config = cryrun_gui.select_config()

        #No config means the user canceled while selecting the config, so we can safely exit.
        if not config:
            sys.exit(0)

        # By default the CMake output is hidden. This is printed to make sure the user knows it's not stuck.
        print("Generating solution...")

        toolchain = config['cmake_toolchain']
        solution_path = os.path.join(project_path, config['cmake_builddir'])
        generator = config['cmake_generator']

        if not os.path.isdir (solution_path):
            os.makedirs (solution_path)

        if toolchain:
            toolchain = toolchain.replace('\\', '/')
            toolchain = os.path.join(cmake_dir, toolchain)

        prepare_cmake_cache(solution_path, generator)

        cmake_command = ['"{}"'.format(cmake_path)]
        cmake_command.append('-Wno-dev')
        if toolchain:
            cmake_command.append('-DCMAKE_TOOLCHAIN_FILE="{}"'.format(toolchain))
        cmake_command.append('"{}"'.format(cmakelists_dir))
        cmake_command.append('-B"{}"'.format(solution_path))
        cmake_command.append('-G"{}"'.format(generator))

        # Filter empty commands, and convert the list to a string.
        cmake_command = list(filter(bool, cmake_command))
        command_str = ("".join("{} ".format(e) for e in cmake_command)).strip()

        try:
            subprocess.check_output(command_str, universal_newlines=True)
        except subprocess.CalledProcessError as e:
            if not e.returncode == 0:
                print("Encountered and error while running command '{}'!".format(command_str))
                print(e.output)
                print("Generating solution has failed!")
                print("Press Enter to exit")
                input()

def prepare_cmake_cache(solution_path, generator):
    """
    Sets the value of the CMAKE_GENERATOR:INTERNAL to the specified generator.
    This fixes an issue when switching between different generators.
    """
    cache_file = os.path.join(solution_path, "CMakeCache.txt")
    if not os.path.isfile(cache_file):
        return

    pattern = "CMAKE_GENERATOR:INTERNAL="
    substitute = "{}{}\n".format(pattern, generator)

    #Create temp file
    fh, abs_path = tempfile.mkstemp()
    with os.fdopen(fh,'w') as new_file:
        with open(cache_file) as old_file:
            for line in old_file:
                if not line.startswith(pattern):
                    new_file.write(line)
                else:
                    new_file.write(substitute)

    #Remove original file
    os.remove(cache_file)

    #Move new file
    shutil.move(abs_path, cache_file)

#--- OPEN ---

def cmd_open (args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    tool_path = os.path.join (get_engine_path(), 'bin', args.platform, 'GameLauncher.exe')
    if not os.path.isfile (tool_path):
        error_engine_tool_not_found (tool_path)

    #---

    subcmd = (
        tool_path,
        '-project',
        os.path.abspath (args.project_file)
    )

    print_subprocess (subcmd)
    subprocess.Popen(subcmd)

#--- DEDICATED SERVER ---

def cmd_launch_dedicated_server (args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    tool_path = os.path.join (get_engine_path(), 'bin', args.platform, 'Game_Server.exe')
    if not os.path.isfile (tool_path):
        error_engine_tool_not_found (tool_path)

    #---

    subcmd = (
        tool_path,
        '-project',
        os.path.abspath (args.project_file)
    )

    print_subprocess (subcmd)
    subprocess.Popen(subcmd)

#--- PACKAGE ---

def cmd_package(argv):
    if not os.path.isfile(args.project_file):
        error_project_not_found(args.project_file)

    release_project.run(args.project_file)


#--- EDIT ---
def cmd_edit(argv):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    tool_path = os.path.join (get_engine_path(), 'bin', args.platform, 'Sandbox.exe')
    if not os.path.isfile (tool_path):
        error_engine_tool_not_found (tool_path)

    #---

    subcmd = (
        tool_path,
        '-project',
        os.path.abspath (args.project_file)
    )

    print_subprocess (subcmd)
    subprocess.Popen(subcmd)

#--- UPGRADE ---

def upgrade_identify50 (project_file):
    dirname = os.path.dirname (project_file)

    listdir = os.listdir (os.path.join (dirname, 'Code'))
    if all ((filename in listdir) for filename in ('CESharp', 'EditorApp', 'Game', 'SandboxInteraction', 'SandboxInteraction.sln')):
        return ('cs', 'SandboxInteraction')

    if all ((filename in listdir) for filename in ('CESharp', 'Game', 'Sydewinder', 'Sydewinder.sln')):
        return ('cs', 'Sydewinder')

    if all ((filename in listdir) for filename in ('CESharp', 'Game')):
        if os.path.isdir (os.path.join (dirname, 'Code', 'CESharp', 'SampleApp')):
            return ('cs', 'Blank')

    if all ((filename in listdir) for filename in ('Game', )):
        if os.path.isdir (os.path.join (dirname, 'Assets', 'levels', 'example')):
            return ('cpp', 'Blank')

    return None

def upgrade_identify51 (project_file):
    dirname = os.path.dirname (project_file)

    listdir = os.listdir (os.path.join (dirname, 'Code'))
    if all ((filename in listdir) for filename in ('Game', 'SampleApp', 'SampleApp.sln')):
        return ('cs', 'Blank')

    if all ((filename in listdir) for filename in ('Game', 'EditorApp', 'SandboxInteraction', 'SandboxInteraction.sln')):
        return ('cs', 'SandboxInteraction')

    if all ((filename in listdir) for filename in ('Game', 'Sydewinder', 'Sydewinder.sln')):
        return ('cs', 'Sydewinder')

    if all ((filename in listdir) for filename in ('Game', )):
        if os.path.isdir (os.path.join (dirname, 'Assets', 'levels', 'example')):
            return ('cpp', 'Blank')

    return None

def cmd_upgrade (args):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    try:
        file = open (args.project_file, 'r')
        project = configparser.ConfigParser()
        project.read_string ('[project]\n' + file.read())
        file.close()
    except ValueError:
        error_project_json_decode (args.project_file)

    engine_version = project['project'].get ('engine_version')
    restore_version = {
    '5.0.0': '5.0.0',
    '5.1.0': '5.1.0',
    '5.1.1': '5.1.0'
    }.get (engine_version)
    if restore_version is None:
        error_upgrade_engine_version (engine_version)

    template_name = None
    if restore_version == '5.0.0':
        template_name = upgrade_identify50 (args.project_file)
    elif restore_version == '5.1.0':
        template_name = upgrade_identify51 (args.project_file)

    if template_name is None:
        error_upgrade_template_unknown (args.project_file)

    restore_path = os.path.abspath (os.path.join (get_tools_path(), 'upgrade', restore_version, *template_name) + '.zip')
    if not os.path.isfile (restore_path):
        error_upgrade_template_missing (restore_path)

    #---

    (dirname, basename) = os.path.split (os.path.abspath (args.project_file))
    prevcwd = os.getcwd()
    os.chdir (dirname)

    if not os.path.isdir ('Backup'):
        os.mkdir ('Backup')
    (fd, zfilename) = tempfile.mkstemp('.zip', datetime.date.today().strftime ('upgrade_%y%m%d_'), os.path.join ('Backup', dirname))
    file = os.fdopen(fd, 'wb')
    backup = zipfile.ZipFile (file, 'w', zipfile.ZIP_DEFLATED)
    restore = zipfile.ZipFile (restore_path, 'r')

    #.bat
    for filename in (basename, 'Code_CPP.bat', 'Code_CS.bat', 'Editor.bat', 'Game.bat', 'CMakeLists.txt'):
        if os.path.isfile (filename):
            backup.write (filename)

    #bin
    for (dirpath, dirnames, filenames) in os.walk ('bin'):
        for filename in filter (lambda a: os.path.splitext(a)[1] in ('.exe', '.dll'), filenames):
            backup.write (os.path.join (dirpath, filename))

    #Solutions
    for (dirpath, dirnames, filenames) in os.walk ('Code'):
        for filename in filter (lambda a: os.path.splitext(a)[1] in ('.sln', '.vcxproj', '.filters', '.user', '.csproj'), filenames):
            path = os.path.join (dirpath, filename)
            backup.write (path)

    backup_list = backup.namelist()

    #Files to be restored
    for filename in restore.namelist():
        if os.path.isfile (filename) and filename not in backup_list:
            backup.write (filename)

    #---

    backup.close()
    file.close()

    #Delete files backed up

    z = zipfile.ZipFile (zfilename, 'r')
    for filename in z.namelist():
        os.chmod(filename, stat.S_IWRITE)
        os.remove (filename)
    z.close()

    restore.extractall()
    restore.close()
    os.chdir (prevcwd)

#--- REQUIRE ---

def require_getall (registry, require_list, result):

    for k in require_list:
        if k in result:
            continue

        project_file = cryregistry.project_file (registry, k)
        project = cryproject.load (project_file)
        result[k] = cryproject.require_list (project)

def require_sortedlist (d):
    d = dict (d)

    result = []
    while d:
        empty = [k for (k, v) in d.items() if len (v) == 0]
        for k in empty:
            del d[k]

        for key in d.keys():
            d[key] = list (filter (lambda k: k not in empty, d[key]))

        empty.sort()
        result.extend (empty)

    return result

def cmd_require (args):
    registry = cryregistry.load()
    project = cryproject.load (args.project_file)

    plugin_dependencies = {}
    require_getall (registry, cryproject.require_list(project), plugin_dependencies)
    plugin_list = require_sortedlist (plugin_dependencies)
    plugin_list = cryregistry.filter_plugin (registry, plugin_list)

    project_path = os.path.dirname (args.project_file)
    plugin_path = os.path.join (project_path, 'cryext.txt')
    if os.path.isfile (plugin_path):
        os.remove (plugin_path)

    plugin_file = open (plugin_path, 'w')
    for k in plugin_list:
        project_file = cryregistry.project_file (registry, k)
        project_path = os.path.dirname (project_file)
        project = cryproject.load (project_file)

        (m_extensionName, shared_path) = cryproject.shared_tuple (project, args.platform, args.config)
        asset_dir = cryproject.asset_dir (project)

        m_extensionBinaryPath = os.path.normpath (os.path.join (project_path, shared_path))
        m_extensionAssetDirectory = asset_dir and os.path.normpath (os.path.join (project_path, asset_dir)) or ''
        m_extensionClassName = 'EngineExtension_%s' % os.path.splitext (os.path.basename (m_extensionBinaryPath))[0]

        line = ';'.join ((m_extensionName, m_extensionClassName, m_extensionBinaryPath, m_extensionAssetDirectory))
        plugin_file.write  (line + os.linesep)

    plugin_file.close()

#--- METAGEN ---

def cmd_metagen(argv):
    if not os.path.isfile (args.project_file):
        error_project_not_found (args.project_file)

    project = cryproject.load (args.project_file)
    if project is None:
        error_project_json_decode (args.project_file)

    tool_path = os.path.join (get_engine_path(), 'Tools/rc/rc.exe')
    if not os.path.isfile (tool_path):
        error_engine_tool_not_found (tool_path)

    job_path = os.path.join (get_engine_path(), 'Tools/cryassets/rcjob_cryassets.xml')
    if not os.path.isfile (job_path):
        error_engine_tool_not_found (job_path)

    project_path = os.path.dirname (os.path.abspath (args.project_file))
    asset_dir = cryproject.asset_dir(project)
    asset_path = os.path.normpath (os.path.join (project_path, asset_dir))

    subcmd = (
        tool_path,
        ('/job=' + job_path),
        ('/src=' + asset_path)
    )

    print_subprocess (subcmd)
    subprocess.Popen(subcmd)

#--- MAIN ---

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument ('--platform', default = 'win_x64', choices = ('win_x86', 'win_x64'))
    parser.add_argument ('--config', default = 'RelWithDebInfo', choices = ('Debug', 'Release', 'RelWithDebInfo', 'MinSizeRel'))

    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True

    parser_upgrade = subparsers.add_parser ('upgrade')
    parser_upgrade.add_argument ('project_file')
    parser_upgrade.add_argument ('--engine_version')
    parser_upgrade.set_defaults(func=cmd_upgrade)

    parser_require = subparsers.add_parser ('require')
    parser_require.add_argument ('project_file')
    parser_require.set_defaults(func=cmd_require)

    parser_projgen = subparsers.add_parser ('projgen')
    parser_projgen.add_argument ('project_file')
    parser_projgen.add_argument ('--buildmachine', action='store_true', default=False)
    parser_projgen.set_defaults(func=cmd_projgen)

    parser_projgen = subparsers.add_parser ('cmake-gui')
    parser_projgen.add_argument ('project_file')
    parser_projgen.set_defaults(func=cmd_cmake_gui)

    parser_build = subparsers.add_parser ('build')
    parser_build.add_argument ('project_file')
    parser_build.set_defaults(func=cmd_build)

    parser_open = subparsers.add_parser ('open')
    parser_open.add_argument ('project_file')
    parser_open.set_defaults(func=cmd_open)

    parser_server = subparsers.add_parser ('server')
    parser_server.add_argument ('project_file')
    parser_server.set_defaults(func=cmd_launch_dedicated_server)

    parser_edit = subparsers.add_parser ('edit')
    parser_edit.add_argument ('project_file')
    parser_edit.set_defaults(func=cmd_edit)

    parser_package = subparsers.add_parser ('package')
    parser_package.add_argument ('project_file')
    parser_package.set_defaults(func=cmd_package)


    parser_edit = subparsers.add_parser ('metagen')
    parser_edit.add_argument ('project_file')
    parser_edit.set_defaults(func=cmd_metagen)

    args = parser.parse_args()
    args.func (args)

