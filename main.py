import math
import os
import sys
import time
import traceback

import keyring
import ntplib
import psutil
import pyotp
import requests
import win32evtlogutil
import win32gui
import win32process
import wx
import wx.adv


PRODUCT_NAME = "XIVLauncher Automatic OTP"
APP_NAME_REALM = "ffxivotp"
CONFIGURE_TEXT = "Configure OTP Secret"
GENERATE_TEXT = "Copy OTP Code"
SEND_TEXT = "Send OTP Code"
SCAN_TEST = "Automatic Code Sending"
CHECK_EVERY_MS = 1 * 1000
SEARCH_PROCESS_NAME = "XIVLauncher.exe"
SEARCH_WINDOW_NAME = "Enter OTP key"
TIMEOUT_TOTP_SEND = 30
CONFIG_PATH = "ffxivotp.ini"

IS_BUILT = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource_path(relative_path):
    if IS_BUILT:
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def create_menu_item(menu, label, func, kind=wx.ITEM_NORMAL):
    item = wx.MenuItem(menu, -1, label, kind=kind)
    menu.Bind(wx.EVT_MENU, func, id=item.GetId())
    menu.Append(item)
    return item


def get_secret():
    return keyring.get_password(APP_NAME_REALM, "secret")


def generate_otp(override=None):
    totp = pyotp.parse_uri(get_secret() or override)
    return totp.now()


def check_clock():
    try:
        client = ntplib.NTPClient()
        res = client.request("pool.ntp.org")

        delta = abs(res.tx_time - time.time())

        if delta >= 5:
            dlg = wx.MessageDialog(
                None,
                "Your PC clock is %.1f seconds out of sync. Generated OTP codes may be incorrect or exploited." % delta,
                PRODUCT_NAME,
                style=wx.ICON_ERROR,
            )
            dlg.ShowModal()

    except Exception as e:
        log_exception(e)
        pass


def log_exception(e):
    tb = traceback.format_exception(None, e, e.__traceback__)

    if IS_BUILT:
        e_type, _, _ = sys.exc_info()
        event_strings = [str(e_type.__name__), str(e)] + tb

        # https://docs.microsoft.com/en-us/windows/win32/eventlog/event-identifiers
        # 718 = "Application popup: %1 : %2" in winerror.h
        win32evtlogutil.ReportEvent(PRODUCT_NAME, 718, strings=event_strings)
    else:
        print(f"[Exception]: {e}")
        print("-" * 80)
        print("\n".join(tb))
        print("-" * 80)


class TaskBarIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        self.config = wx.Config(APP_NAME_REALM)

        self.do_scan = self.config.ReadBool("do_scan", True)
        self.tick_lock = False

        self.check_after = 0
        self.closing = False

        self.frame = frame
        super(TaskBarIcon, self).__init__()
        self.set_icon(resource_path("icon.ico"))
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.on_click)

        self.timer = wx.Timer(self)
        self.timer.Start(CHECK_EVERY_MS)
        self.Bind(wx.EVT_TIMER, self.on_tick)

        self.ShowBalloon(PRODUCT_NAME, PRODUCT_NAME + " started. Right click tray icon to configure.")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        create_menu_item(menu, CONFIGURE_TEXT, self.on_setup)

        scanCheckItem = create_menu_item(menu, SCAN_TEST, self.on_tickbox, kind=wx.ITEM_CHECK)
        scanCheckItem.Check(self.do_scan)
        menu.AppendSeparator()

        create_menu_item(menu, GENERATE_TEXT, self.on_generate)
        create_menu_item(menu, SEND_TEXT, self.on_send)
        menu.AppendSeparator()
        create_menu_item(menu, "Exit", self.on_exit)
        return menu

    def show_balloon(self, str):
        if IS_BUILT:
            self.ShowBalloon(PRODUCT_NAME, str)
        else:
            print(f"[{PRODUCT_NAME}]: {str}")

    def set_icon(self, path):
        icon = wx.Icon(path)
        self.SetIcon(icon, PRODUCT_NAME)

    def on_tickbox(self, event):
        self.do_scan = not self.do_scan
        self.config.WriteBool("do_scan", self.do_scan)
        self.ShowBalloon(PRODUCT_NAME, f"{SCAN_TEST} was {'enabled' if self.do_scan else 'disabled'}.")
        self.tick_lock = False

    def on_tick(self, event):
        if self.tick_lock:
            return

        self.tick_lock = True

        if not self.do_scan or self.check_after > time.time():
            self.tick_lock = False
            return

        active_window = win32gui.GetForegroundWindow()

        if win32gui.GetWindowText(active_window).lower() != SEARCH_WINDOW_NAME.lower():
            self.tick_lock = False
            return

        _, active_window_pid = win32process.GetWindowThreadProcessId(active_window)
        active_window_pname = psutil.Process(active_window_pid).name().lower()

        if active_window_pname != SEARCH_PROCESS_NAME.lower():
            self.tick_lock = False
            return

        self.on_send(event, True)

        self.check_after = time.time() + TIMEOUT_TOTP_SEND
        self.tick_lock = False

    def on_click(self, event):
        if get_secret():
            self.on_generate(event)
        else:
            self.on_setup(event)

    def on_setup(self, event):
        if get_secret():
            erase_config_msg_dialog = wx.MessageDialog(
                None,
                "A OTP secret has already been saved. Would you like to erase it?",
                CONFIGURE_TEXT,
                style=wx.ICON_WARNING | wx.YES_NO | wx.CANCEL | wx.NO_DEFAULT,
            )
            erase_config_msg_modal = erase_config_msg_dialog.ShowModal()

            if erase_config_msg_modal != wx.ID_YES:
                return

            text = ""
            while text.lower() != "confirm":
                erase_config_text_dialog = wx.TextEntryDialog(
                    None,
                    'Are you sure you would like to erase your OTP secret? Type "confirm" to confirm.',
                    CONFIGURE_TEXT,
                    text,
                )
                erase_config_text_modal = erase_config_text_dialog.ShowModal()

                if erase_config_text_modal != wx.ID_OK:
                    return

                text = erase_config_text_dialog.GetValue().lower()

            keyring.delete_password(APP_NAME_REALM, "secret")

            erase_confirm_msg_dialog = wx.MessageDialog(None, "OTP secret erased.", CONFIGURE_TEXT)
            erase_confirm_msg_dialog.ShowModal()
            return

        setup_dialog = wx.PasswordEntryDialog(None, "Please enter the OTP secret URL:", CONFIGURE_TEXT)
        rc1 = setup_dialog.ShowModal()

        if rc1 == wx.ID_OK:
            secret = setup_dialog.GetValue()

            if not (secret.startswith("otpauth://")) or not generate_otp(secret):
                invalid_dialog = wx.MessageDialog(None, "Invalid secret.", CONFIGURE_TEXT, style=wx.ICON_ERROR)
                invalid_dialog.ShowModal()
                return self.on_setup(event)

            keyring.set_password(APP_NAME_REALM, "secret", secret)

            confirm_dialog = wx.MessageDialog(None, "Secret saved.", CONFIGURE_TEXT)
            confirm_dialog.ShowModal()

    def on_generate(self, event):
        if not get_secret():
            dialog = wx.MessageDialog(
                None,
                "The OTP secret has not yet been configured. Configure a secret first.",
                GENERATE_TEXT,
                style=wx.ICON_WARNING,
            )
            dialog.ShowModal()
            return

        self.ShowBalloon(PRODUCT_NAME, "Generating OTP code...")

        try:
            if wx.TheClipboard.Open() or wx.TheClipboard.Open():
                check_clock()

                wx.TheClipboard.SetData(wx.TextDataObject(generate_otp()))
                wx.TheClipboard.Close()

                self.ShowBalloon(PRODUCT_NAME, "OTP code copied to clipboard!")
                return
            else:
                raise OSError("Unable to open clipboard.")

        except Exception as e:
            self.ShowBalloon(PRODUCT_NAME, "Error copying OTP code")
            log_exception(e)
            pass

    def on_send(self, event, auto=False):
        if not get_secret():
            if not auto:
                dialog = wx.MessageDialog(
                    None,
                    "The OTP secret has not yet been configured. Configure a secret first.",
                    GENERATE_TEXT,
                    style=wx.ICON_WARNING,
                )
                dialog.ShowModal()

            return

        self.ShowBalloon(PRODUCT_NAME, "Sending OTP code...")

        try:
            check_clock()

            response = requests.get(f"http://localhost:4646/ffxivlauncher/{generate_otp()}")
            response.raise_for_status()

            self.ShowBalloon(PRODUCT_NAME, "OTP code sent!")
        except Exception as e:
            log_exception(e)
            self.ShowBalloon(PRODUCT_NAME, "Error sending OTP code")
            return

    def on_exit(self, event):
        self.closing = True
        wx.CallAfter(self.Destroy)
        self.frame.Close()


class App(wx.App):
    def OnInit(self):
        frame = wx.Frame(None)
        self.SetTopWindow(frame)
        TaskBarIcon(frame)
        return True


def main():
    app = App(False)
    app.MainLoop()


if __name__ == "__main__":
    main()
