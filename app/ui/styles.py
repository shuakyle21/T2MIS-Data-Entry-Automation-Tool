"""Central stylesheet for the T2MIS Encoder desktop app."""

APP_QSS = """
QMainWindow {
    background: #eef2f7;
}
QWidget {
    font-size: 13px;
    color: #0f172a;
}
QLabel#AppTitle {
    font-size: 18px;
    font-weight: 800;
    color: #0f172a;
}
QLabel#AppSubtitle {
    font-size: 12px;
    color: #64748b;
}
QFrame#Card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
}
QLabel#SectionTitle {
    font-size: 12px;
    font-weight: 700;
    color: #334155;
    letter-spacing: 0.4px;
}
QLabel#CountBadge {
    background: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 700;
    font-size: 12px;
}
QLabel#FilePath {
    color: #475569;
    font-size: 12px;
}
QLabel#StatusBanner {
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 600;
    font-size: 13px;
}
QLabel#StatusBanner[kind="idle"] {
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #e2e8f0;
}
QLabel#StatusBanner[kind="working"] {
    background: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
}
QLabel#StatusBanner[kind="review"] {
    background: #fffbeb;
    color: #92400e;
    border: 1px solid #fde68a;
}
QLabel#StatusBanner[kind="error"] {
    background: #fef2f2;
    color: #b91c1c;
    border: 1px solid #fecaca;
}
QLabel#StatusBanner[kind="ok"] {
    background: #f0fdf4;
    color: #15803d;
    border: 1px solid #bbf7d0;
}
QPushButton {
    padding: 7px 14px;
    border-radius: 7px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    font-weight: 600;
}
QPushButton:hover:!disabled {
    border-color: #94a3b8;
    background: #f8fafc;
}
QPushButton:disabled {
    color: #94a3b8;
    border-color: #e2e8f0;
    background: #f1f5f9;
}
QPushButton#PrimaryButton {
    background: #2563eb;
    border: none;
    color: white;
}
QPushButton#PrimaryButton:hover:!disabled {
    background: #1d4ed8;
}
QPushButton#PrimaryButton:disabled {
    background: #93c5fd;
    color: white;
}
QPushButton#SuccessButton {
    background: #16a34a;
    border: none;
    color: white;
}
QPushButton#SuccessButton:hover:!disabled {
    background: #15803d;
}
QPushButton#SuccessButton:disabled {
    background: #86efac;
    color: white;
}
QPushButton#DangerButton {
    color: #b91c1c;
    border-color: #fecaca;
}
QPushButton#DangerButton:hover:!disabled {
    background: #fef2f2;
    border-color: #fca5a5;
}
QLineEdit, QComboBox, QDateEdit {
    padding: 6px 10px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    background: white;
    selection-background-color: #dbeafe;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {
    border-color: #2563eb;
}
QTableView {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: white;
    gridline-color: #f1f5f9;
    alternate-background-color: #f8fafc;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
    outline: 0;
}
QHeaderView::section {
    background: #f8fafc;
    color: #475569;
    border: none;
    border-bottom: 1px solid #e2e8f0;
    border-right: 1px solid #f1f5f9;
    padding: 7px 8px;
    font-weight: 700;
    font-size: 12px;
}
QProgressBar {
    height: 10px;
    border-radius: 5px;
    background: #e2e8f0;
    text-align: center;
    font-size: 11px;
    color: #475569;
}
QProgressBar::chunk {
    border-radius: 5px;
    background: #2563eb;
}
QTextEdit#LogView {
    background: #0f172a;
    color: #e2e8f0;
    border: 1px solid #1e293b;
    border-radius: 8px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 12px;
    padding: 6px;
}
QCheckBox {
    spacing: 4px;
}
QStatusBar {
    background: #eef2f7;
    color: #64748b;
    font-size: 12px;
}
"""
