import ctypes
import os
import sys
from urllib.parse import quote

try:
    import winreg
except ImportError:  # pragma: no cover - модуль доступен только в Windows
    winreg = None


REGISTERED_APP_NAME = "Log Analyzer"
CAPABILITIES_PATH = r"Software\LogAnalyzer\Capabilities"
APPLICATION_PATH = r"Software\Classes\Applications\LogAnalyzer.exe"
FILE_TYPES = {
    ".log": ("LogAnalyzer.LogFile.1", "Log Analyzer log file"),
    ".zip": ("LogAnalyzer.ZipFile.1", "Log Analyzer ZIP archive"),
}


def _delete_value(key, name):
    try:
        winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(winreg.EnumKey(key, index))
                    index += 1
                except OSError:
                    break
        for child in children:
            _delete_tree(root, path + "\\" + child)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _set_string(root, path, name, value):
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _register_file_type(extension, executable):
    prog_id, description = FILE_TYPES[extension]
    classes = r"Software\Classes"
    command = f'"{executable}" "%1"'

    _set_string(winreg.HKEY_CURRENT_USER, classes + "\\" + prog_id, "", description)
    _set_string(
        winreg.HKEY_CURRENT_USER,
        classes + "\\" + prog_id + r"\DefaultIcon",
        "",
        f'"{executable}",0',
    )
    _set_string(
        winreg.HKEY_CURRENT_USER,
        classes + "\\" + prog_id + r"\shell\open\command",
        "",
        command,
    )
    with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            classes + "\\" + extension + r"\OpenWithProgids",
            0,
            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, prog_id, 0, winreg.REG_NONE, b"")


def _unregister_file_type(extension):
    prog_id, _description = FILE_TYPES[extension]
    classes = r"Software\Classes"
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                classes + "\\" + extension + r"\OpenWithProgids",
                0,
                winreg.KEY_WRITE) as key:
            _delete_value(key, prog_id)
    except FileNotFoundError:
        pass
    _delete_tree(winreg.HKEY_CURRENT_USER, classes + "\\" + prog_id)


def _register_application(executable, enabled_extensions):
    command = f'"{executable}" "%1"'
    _set_string(
        winreg.HKEY_CURRENT_USER,
        r"Software\RegisteredApplications",
        REGISTERED_APP_NAME,
        CAPABILITIES_PATH,
    )
    _set_string(
        winreg.HKEY_CURRENT_USER,
        CAPABILITIES_PATH,
        "ApplicationName",
        REGISTERED_APP_NAME,
    )
    _set_string(
        winreg.HKEY_CURRENT_USER,
        CAPABILITIES_PATH,
        "ApplicationDescription",
        "Просмотр и анализ файлов журналов",
    )
    _set_string(
        winreg.HKEY_CURRENT_USER,
        APPLICATION_PATH,
        "FriendlyAppName",
        REGISTERED_APP_NAME,
    )
    _set_string(
        winreg.HKEY_CURRENT_USER,
        APPLICATION_PATH + r"\shell\open\command",
        "",
        command,
    )

    capabilities = CAPABILITIES_PATH + r"\FileAssociations"
    supported = APPLICATION_PATH + r"\SupportedTypes"
    for extension, (prog_id, _description) in FILE_TYPES.items():
        if extension in enabled_extensions:
            _set_string(winreg.HKEY_CURRENT_USER, capabilities, extension, prog_id)
            _set_string(winreg.HKEY_CURRENT_USER, supported, extension, "")
        else:
            for path in (capabilities, supported):
                try:
                    with winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
                        _delete_value(key, extension)
                except FileNotFoundError:
                    pass


def _unregister_application():
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\RegisteredApplications",
                0,
                winreg.KEY_WRITE) as key:
            _delete_value(key, REGISTERED_APP_NAME)
    except FileNotFoundError:
        pass
    _delete_tree(winreg.HKEY_CURRENT_USER, CAPABILITIES_PATH)
    _delete_tree(winreg.HKEY_CURRENT_USER, APPLICATION_PATH)


def _notify_shell():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


def sync_file_associations(associate_log=True, associate_zip=False, executable=None):
    if os.name != "nt" or winreg is None:
        return False
    if executable is None:
        if not getattr(sys, "frozen", False):
            return False
        executable = sys.executable

    executable = os.path.abspath(executable)
    enabled = set()
    if associate_log:
        enabled.add(".log")
    if associate_zip:
        enabled.add(".zip")

    for extension in FILE_TYPES:
        if extension in enabled:
            _register_file_type(extension, executable)
        else:
            _unregister_file_type(extension)

    if enabled:
        _register_application(executable, enabled)
    else:
        _unregister_application()
    _notify_shell()
    return True


def open_default_apps_settings():
    if os.name != "nt":
        return False
    uri = "ms-settings:defaultapps?registeredAppUser=" + quote(REGISTERED_APP_NAME)
    os.startfile(uri)
    return True
