"""Read-only tables update records once instead of creating per-cell widgets."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView


class RecordModel(QAbstractTableModel):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers = tuple(headers)
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        values, record = self.rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return values[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return record
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def replace(self, rows):
        if rows == self.rows:
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True


class RecordTable(QTableView):
    selection_changed = Signal()

    def __init__(self, headers):
        super().__init__()
        self._record_model = RecordModel(headers, self)
        self.setModel(self._record_model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().hide()
        self.horizontalHeader().setResizeContentsPrecision(100)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setAlternatingRowColors(True)
        self.selectionModel().selectionChanged.connect(lambda *args: self.selection_changed.emit())

    def selected_record(self):
        rows = self.selectionModel().selectedRows()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def replace_records(self, rows, key):
        previous = self.selected_record()
        was_blocked = self.blockSignals(True)
        try:
            changed = self.model().replace(rows)
            if changed and previous:
                for index, (_, record) in enumerate(rows):
                    if record[key] == previous[key]:
                        self.selectRow(index)
                        break
        finally:
            self.blockSignals(was_blocked)
