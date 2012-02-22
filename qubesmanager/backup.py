#!/usr/bin/python2.6
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2012  Agnieszka Kostrzewa <agnieszka.kostrzewa@gmail.com>
# Copyright (C) 2012  Marek Marczykowski <marmarek@mimuw.edu.pl>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#

import sys
import os
from PyQt4.QtCore import *
from PyQt4.QtGui import *

from qubes.qubes import QubesVmCollection
from qubes.qubes import QubesException
from qubes.qubes import QubesDaemonPidfile
from qubes.qubes import QubesHost
from qubes import qubesutils

import qubesmanager.resources_rc

from pyinotify import WatchManager, Notifier, ThreadedNotifier, EventsCodes, ProcessEvent

import subprocess
import time
from thread_monitor import *
from operator import itemgetter

from datetime import datetime
from string import replace

from ui_backupdlg import *
from multiselectwidget import *

from backup_utils import *


class BackupVMsWindow(Ui_Backup, QWizard):

    __pyqtSignals__ = ("backup_progress(int)",)

    excluded = []
    to_backup = []

    def __init__(self, app, qvm_collection, blk_manager, parent=None):
        super(BackupVMsWindow, self).__init__(parent)

        self.app = app
        self.qvm_collection = qvm_collection
        self.blk_manager = blk_manager

        self.dev_mount_path = None
        self.backup_dir = None
        self.func_output = []

        for vm in self.qvm_collection.values():
            if vm.qid == 0:
                self.vm = vm
                break;
        
        assert self.vm != None

        self.setupUi(self)

        self.dir_line_edit.setReadOnly(True)

        self.select_vms_widget = MultiSelectWidget(self)
        self.verticalLayout.insertWidget(1, self.select_vms_widget)

        self.connect(self, SIGNAL("currentIdChanged(int)"), self.current_page_changed)
        self.connect(self.dev_combobox, SIGNAL("activated(int)"), self.dev_combobox_activated)
        self.connect(self, SIGNAL("backup_progress(int)"), self.progress_bar.setValue)

        self.select_vms_page.isComplete = self.has_selected_vms
        self.select_dir_page.isComplete = self.has_selected_dir
        #FIXME
        #this causes to run isComplete() twice, I don't know why
        self.select_vms_page.connect(self.select_vms_widget, SIGNAL("selected_changed()"), SIGNAL("completeChanged()")) 

        self.__fill_vms_list__()
        fill_devs_list(self)

    def __fill_vms_list__(self):
        for vm in self.qvm_collection.values():
            if vm.is_running() and vm.qid != 0:
                self.excluded.append(vm.name)
                continue
            
            if vm.is_appvm() and vm.internal:
                self.excluded.append(vm.name)
                continue

            if vm.is_template() and vm.installed_by_rpm:
                self.excluded.append(vm.name)
                continue

            self.to_backup.append(vm.name)
            self.select_vms_widget.available_list.addItem(vm.name)

 
    def dev_combobox_activated(self, idx):
        dev_combobox_activated(self, idx)
                   

    @pyqtSlot(name='on_select_path_button_clicked')
    def select_path_button_clicked(self):
        select_path_button_clicked(self)

    def validateCurrentPage(self):
        if self.currentPage() is self.select_vms_page:
            for i in range(self.select_vms_widget.available_list.count()):
                vmname =  self.select_vms_widget.available_list.item(i).text()
                self.excluded.append(vmname)
        return True

    def gather_output(self, s):
        self.func_output.append(s)

    def update_progress_bar(self, value):
        self.emit(SIGNAL("backup_progress(int)"), value)


    def __do_backup__(self, thread_monitor):
        msg = []
        try:
            qubesutils.backup_do(str(self.backup_dir), self.files_to_backup, self.update_progress_bar)
            #simulate_long_lasting_proces(10, self.update_progress_bar) 
        except Exception as ex:
            msg.append(str(ex))

        if len(msg) > 0 :
            thread_monitor.set_error_msg('\n'.join(msg))

        thread_monitor.set_finished()

    
    def current_page_changed(self, id):
        if self.currentPage() is self.confirm_page:
            del self.func_output[:]
            self.files_to_backup = qubesutils.backup_prepare(str(self.backup_dir), exclude_list = self.excluded, print_callback = self.gather_output)

            self.textEdit.setReadOnly(True)
            self.textEdit.setFontFamily("Monospace")
            self.textEdit.setText("\n".join(self.func_output))

        elif self.currentPage() is self.commit_page:
            self.button(self.CancelButton).setDisabled(True)
            self.button(self.FinishButton).setDisabled(True)
            self.thread_monitor = ThreadMonitor()
            thread = threading.Thread (target= self.__do_backup__ , args=(self.thread_monitor,))
            thread.daemon = True
            thread.start()

            while not self.thread_monitor.is_finished():
                self.app.processEvents()
                time.sleep (0.1)

            if not self.thread_monitor.success:
                QMessageBox.warning (None, "Backup error!", "ERROR: {1}".format(self.vm.name, self.thread_monitor.error_msg))

            umount_device(self.dev_mount_path)
            self.button(self.FinishButton).setEnabled(True)
 
    def has_selected_vms(self):
        return self.select_vms_widget.selected_list.count() > 0

    def has_selected_dir(self):
        return self.backup_dir != None
            



# Bases on the original code by:
# Copyright (c) 2002-2007 Pascal Varet <p.varet@gmail.com>

def handle_exception( exc_type, exc_value, exc_traceback ):
    import sys
    import os.path
    import traceback

    filename, line, dummy, dummy = traceback.extract_tb( exc_traceback ).pop()
    filename = os.path.basename( filename )
    error    = "%s: %s" % ( exc_type.__name__, exc_value )

    QMessageBox.critical(None, "Houston, we have a problem...",
                         "Whoops. A critical error has occured. This is most likely a bug "
                         "in Qubes Restore VMs application.<br><br>"
                         "<b><i>%s</i></b>" % error +
                         "at <b>line %d</b> of file <b>%s</b>.<br/><br/>"
                         % ( line, filename ))




def main():

    global qubes_host
    qubes_host = QubesHost()

    global app
    app = QApplication(sys.argv)
    app.setOrganizationName("The Qubes Project")
    app.setOrganizationDomain("http://qubes-os.org")
    app.setApplicationName("Qubes Backup VMs")

    sys.excepthook = handle_exception

    qvm_collection = QubesVmCollection()
    qvm_collection.lock_db_for_reading()
    qvm_collection.load()
    qvm_collection.unlock_db()

    global backup_window
    backup_window = BackupVMsWindow()

    backup_window.show()

    app.exec_()
    app.exit()



if __name__ == "__main__":
    main()