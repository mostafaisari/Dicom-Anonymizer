import sys
import os
import time
import re
import shutil
import zipfile
import tempfile
import pydicom
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

# تگ‌های حساس و استاندارد ناشناس‌سازی
STANDARD_ANON_TAGS = [
    "PatientName","PatientID","PatientBirthDate","PatientSex",
    "OtherPatientIDs","OtherPatientNames","PatientAddress","PatientTelephoneNumbers",
    "InstitutionName","InstitutionAddress","ReferringPhysicianName",
    "StudyDescription","SeriesDescription","AccessionNumber","StudyID",
    "OperatorsName","PerformingPhysicianName"
]

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split('(\d+)', s)]

# -----------------------------
# Worker برای پردازش فایل‌ها
# -----------------------------
class AnonymizeWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(int)
    eta_signal = pyqtSignal(str)

    def __init__(self, files, source, dest, tags, zip_mode=False, temp_zip_dir=None):
        super().__init__()
        self.files = files
        self.source = source
        self.dest = dest
        self.tags = tags
        self.zip_mode = zip_mode
        self.temp_zip_dir = temp_zip_dir
        self.changed_items = {}
        self.success_count = 0

    def safe_assign_name_only(self, dataset, tag_name, value):
        if not hasattr(dataset, tag_name):
            return
        final_value = value if "Name" in tag_name else ""
        old_value = str(getattr(dataset, tag_name))
        setattr(dataset, tag_name, final_value)
        if old_value != final_value:
            self.changed_items[tag_name] = final_value

    def process_dataset(self, dataset):
        for tag_name, value in self.tags.items():
            if "." not in tag_name:
                self.safe_assign_name_only(dataset, tag_name, value)
        for elem in dataset:
            if elem.VR == "SQ":
                for sub_ds in elem.value:
                    self.process_dataset(sub_ds)

    def get_full_path(self, file_name):
        if self.zip_mode:
            return os.path.join(self.temp_zip_dir, file_name)
        else:
            return os.path.join(self.source, file_name)

    def process_file(self, file):
        try:
            filepath = self.get_full_path(file)
            ds = pydicom.dcmread(filepath, force=True)
            self.process_dataset(ds)
            # ایجاد فولدر dicom_Anonymize در مسیر مقصد
            output_dir = os.path.join(self.dest, "dicom_Anonymize")
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, os.path.basename(file))
            ds.save_as(out_path, write_like_original=False)
            self.success_count += 1
        except Exception as e:
            print(f"Skipped {file} -> {e}")

    def run(self):
        total = len(self.files)
        start_time = time.time()
        done = 0

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.process_file, f) for f in self.files]
            for future in futures:
                future.result()
                done += 1
                percent = int((done / total) * 100)
                self.progress.emit(percent)
                elapsed = time.time() - start_time
                avg = elapsed / done if done else 0
                remaining = avg * (total - done)
                self.eta_signal.emit(f"ETA: {int(remaining)} sec")

        end_time = time.time()
        duration = round(end_time - start_time, 2)

        # -----------------------------
        # تولید لاگ حرفه‌ای در مسیر اصلی مقصد
        # -----------------------------
        if self.dest:
            log_path = os.path.join(self.dest, "anonymize_log.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("========================================\n")
                f.write("DICOM Anonymization Report\n")
                f.write("========================================\n\n")
                f.write(f"Source Path      : {self.source}\n")
                f.write(f"Destination Path : {self.dest}\n\n")
                f.write(f"Selected Files   : {total}\n")
                f.write(f"Output Files     : {self.success_count}\n")
                f.write(f"Operation Time   : {duration} sec\n\n")
                f.write("Changed Items:\n")
                if self.changed_items:
                    for tag, value in self.changed_items.items():
                        f.write(f" - {tag}  -->  '{value}'\n")
                else:
                    f.write(" - No changes applied\n")
                f.write("\n========================================\n")

        self.finished.emit(self.success_count)

# -----------------------------
# GUI اصلی برنامه
# -----------------------------
class DicomAnonymizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Professional DICOM Anonymizer")
        self.resize(1400, 900)
        self.source_path = ""
        self.dest_path = ""
        self.default_tags = STANDARD_ANON_TAGS.copy()
        self.custom_replacements = {}
        self.all_tags = []
        self.zip_mode = False
        self.subfolder_mode = False
        self.temp_zip_dir = None
        self.build_ui()

    def build_ui(self):
        main_layout = QVBoxLayout()

        self.header_table = QTableWidget()
        self.header_table.setColumnCount(3)
        self.header_table.setHorizontalHeaderLabels(["Select","Tag","Value"])
        self.header_table.horizontalHeader().setStretchLastSection(True)
        main_layout.addWidget(QLabel("DICOM Header"))
        main_layout.addWidget(self.header_table,3)

        main_layout.addWidget(QLabel("DICOM Files"))
        self.file_count_label = QLabel("Total: 0 | Selected: 0")
        main_layout.addWidget(self.file_count_label)
        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self.display_header)
        main_layout.addWidget(self.file_list,2)

        path_layout = QHBoxLayout()
        self.source_box = QLineEdit()
        self.dest_box = QLineEdit()
        btn_source = QPushButton("Select Source")
        self.chk_zip = QCheckBox("Zip")
        self.chk_subfolder = QCheckBox("Sub_Folder")
        btn_dest = QPushButton("Select Destination")
        btn_source.clicked.connect(self.select_source)
        btn_dest.clicked.connect(self.select_dest)
        path_layout.addWidget(btn_source)
        path_layout.addWidget(self.source_box)
        path_layout.addWidget(self.chk_zip)
        path_layout.addWidget(self.chk_subfolder)
        path_layout.addWidget(btn_dest)
        path_layout.addWidget(self.dest_box)
        main_layout.addLayout(path_layout)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.eta_label = QLabel("ETA: -")
        btn_run = QPushButton("Run")
        btn_config = QPushButton("Config")
        btn_run.clicked.connect(self.run_process)
        btn_config.clicked.connect(self.open_config)
        bottom.addWidget(btn_run)
        bottom.addWidget(btn_config)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.eta_label)
        main_layout.addLayout(bottom)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    # -----------------------------
    # انتخاب مسیر Source
    # -----------------------------
    def select_source(self):
        self.zip_mode = self.chk_zip.isChecked()
        self.subfolder_mode = self.chk_subfolder.isChecked()
        if self.zip_mode:
            path, _ = QFileDialog.getOpenFileName(self,"Select Zip File", "","Zip Files (*.zip)")
            if path:
                self.source_path = path
                self.source_box.setText(path)
                if self.temp_zip_dir:
                    shutil.rmtree(self.temp_zip_dir)
                self.temp_zip_dir = tempfile.mkdtemp()
                with zipfile.ZipFile(path,"r") as zip_ref:
                    zip_ref.extractall(self.temp_zip_dir)
                self.load_files(zip_mode=True)
        else:
            folder = QFileDialog.getExistingDirectory(self,"Select Source Folder")
            if folder:
                self.source_path = folder
                self.source_box.setText(folder)
                self.load_files(zip_mode=False)

    # -----------------------------
    # انتخاب مسیر Destination
    # -----------------------------
    def select_dest(self):
        folder = QFileDialog.getExistingDirectory(self,"Select Destination")
        if folder:
            self.dest_path = folder
            self.dest_box.setText(folder)

    # -----------------------------
    # بارگذاری فایل‌ها
    # -----------------------------
    def load_files(self, zip_mode=False):
        self.file_list.clear()
        files = []

        if zip_mode:
            for root, dirs, fs in os.walk(self.temp_zip_dir):
                for f in fs:
                    if f.lower().endswith(".dcm"):
                        rel_path = os.path.relpath(os.path.join(root,f), self.temp_zip_dir)
                        files.append(rel_path)
        else:
            if self.subfolder_mode:
                for root, dirs, fs in os.walk(self.source_path):
                    for f in fs:
                        if f.lower().endswith(".dcm"):
                            rel_path = os.path.relpath(os.path.join(root,f), self.source_path)
                            files.append(rel_path)
            else:
                files = [f for f in os.listdir(self.source_path) if f.lower().endswith(".dcm")]

        files.sort(key=natural_sort_key)
        for f in files:
            item = QListWidgetItem(f)
            item.setCheckState(Qt.Checked)
            self.file_list.addItem(item)

        self.file_count_label.setText(f"Total: {len(files)} | Selected: {len(files)}")
        if self.file_list.count() > 0:
            self.file_list.setCurrentRow(0)
            self.display_header(self.file_list.item(0))

    def get_full_path(self, file_name):
        if self.zip_mode:
            return os.path.join(self.temp_zip_dir, file_name)
        else:
            return os.path.join(self.source_path, file_name)

    # -----------------------------
    # نمایش Header فایل انتخاب شده
    # -----------------------------
    def display_header(self, item):
        try:
            filepath = self.get_full_path(item.text())
            ds = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
        except:
            return

        self.header_table.setRowCount(0)
        self.all_tags.clear()
        row = 0

        def add_dataset(dataset, prefix=""):
            nonlocal row
            for elem in dataset:
                tag_name = elem.keyword if elem.keyword else str(elem.tag)
                full_tag = prefix + tag_name

                if elem.VR == "SQ":
                    self.header_table.insertRow(row)
                    tag_item = QTableWidgetItem(f"[Sequence] {full_tag}")
                    tag_item.setForeground(Qt.blue)
                    self.header_table.setItem(row,1,tag_item)
                    row += 1
                    for i, sub_ds in enumerate(elem.value):
                        add_dataset(sub_ds, prefix=full_tag+f"[{i}].")
                else:
                    self.all_tags.append(full_tag)
                    self.header_table.insertRow(row)

                    select_item = QTableWidgetItem()
                    select_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                    select_item.setCheckState(Qt.Checked if tag_name in self.default_tags else Qt.Unchecked)

                    tag_item = QTableWidgetItem(full_tag)
                    if tag_name in STANDARD_ANON_TAGS:
                        tag_item.setForeground(Qt.red)

                    original_value = str(elem.value)

                    # 🔥 این خط اضافه شده — اعمال پایدار replacement برای همه فایل‌ها
                    if full_tag in self.custom_replacements:
                        display_value = self.custom_replacements[full_tag]
                    else:
                        display_value = original_value

                    value_item = QTableWidgetItem(display_value)
                    value_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsEnabled)
                    value_item.original_value = original_value

                    self.header_table.setItem(row,0,select_item)
                    self.header_table.setItem(row,1,tag_item)
                    self.header_table.setItem(row,2,value_item)

                    row += 1

        if hasattr(ds,"file_meta"):
            add_dataset(ds.file_meta,"FileMeta.")
        add_dataset(ds)

    # -----------------------------
    # اجرای عملیات ناشناس‌سازی
    # -----------------------------
    def run_process(self):
        selected_files = [self.file_list.item(i).text() for i in range(self.file_list.count())
                          if self.file_list.item(i).checkState()==Qt.Checked]

        tags = {}
        for row in range(self.header_table.rowCount()):
            select_item = self.header_table.item(row,0)
            tag_item = self.header_table.item(row,1)
            value_item = self.header_table.item(row,2)
            if not select_item or not tag_item or not value_item:
                continue
            if select_item.checkState()==Qt.Checked:
                tag = tag_item.text()
                value_str = value_item.text().strip()
                if hasattr(value_item,"original_value"):
                    tags[tag] = value_str if value_str != value_item.original_value else ""
                else:
                    tags[tag] = ""

        self.worker = AnonymizeWorker(
            selected_files,
            self.source_path,
            self.dest_path,
            tags,
            zip_mode=self.zip_mode,
            temp_zip_dir=self.temp_zip_dir
        )
        self.worker.progress.connect(self.progress.setValue)
        self.worker.eta_signal.connect(self.eta_label.setText)
        self.worker.finished.connect(
            lambda c: QMessageBox.information(
                self,
                "Done",
                f"{c} files processed.\nLog saved in destination folder."
            )
        )
        self.worker.start()

    # -----------------------------
    # Config برای پیش‌فرض‌ها و Replacement
    # -----------------------------
    def open_config(self):
        win = QDialog(self)
        win.setWindowTitle("Config")
        win.resize(500,600)
        layout = QVBoxLayout()

        search_box = QLineEdit()
        search_box.setPlaceholderText("Search tag...")
        layout.addWidget(search_box)

        replace_layout = QHBoxLayout()
        replace_input = QLineEdit()
        replace_input.setPlaceholderText("Replacement value...")
        btn_apply = QPushButton("Apply")
        replace_layout.addWidget(replace_input)
        replace_layout.addWidget(btn_apply)
        layout.addLayout(replace_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        vbox = QVBoxLayout(container)
        checkboxes = {}

        for tag in self.all_tags:
            cb = QCheckBox(tag)
            cb.setChecked(tag in self.default_tags)
            if tag in STANDARD_ANON_TAGS:
                cb.setStyleSheet("color:red;")
            checkboxes[tag] = cb
            vbox.addWidget(cb)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        def filter_tags():
            text = search_box.text().lower()
            for tag, cb in checkboxes.items():
                cb.setVisible(text in tag.lower())

        search_box.textChanged.connect(filter_tags)

       
        def apply_value():
            value = replace_input.text().strip()

            for tag, cb in checkboxes.items():
                if cb.isChecked():
                    
                    self.custom_replacements[tag] = value if "Name" in tag else ""

          
            current_item = self.file_list.currentItem()
            if current_item:
                self.display_header(current_item)

        btn_apply.clicked.connect(apply_value)

        def save():
            self.default_tags = [t for t,cb in checkboxes.items() if cb.isChecked()]
            for row in range(self.header_table.rowCount()):
                tag_item = self.header_table.item(row,1)
                select_item = self.header_table.item(row,0)
                value_item = self.header_table.item(row,2)
                if not tag_item or not select_item:
                    continue
                tag = tag_item.text()
                select_item.setCheckState(Qt.Checked if tag in self.default_tags else Qt.Unchecked)
                if tag in self.custom_replacements and value_item:
                    value_item.setText(self.custom_replacements[tag])
            win.accept()

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(save)
        layout.addWidget(btn_save)

        win.setLayout(layout)
        win.exec_()

# -----------------------------
# اجرای برنامه
# -----------------------------
if __name__=="__main__":
    app = QApplication(sys.argv)
    window = DicomAnonymizer()
    window.show()
    sys.exit(app.exec_())
