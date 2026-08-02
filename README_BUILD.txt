ELECTION PDF CONVERTER — WINDOWS EXE BUILDER
================================================

WHAT THIS PACKAGE DOES
- Builds a Windows 10/11 64-bit desktop application.
- The client only sees ElectionPDFConverter.exe / the converter interface.
- Python source files are not needed in the client-delivery folder.
- Tesseract OCR and Hindi/English language data are bundled into the app.

RECOMMENDED: CREATE ONE SETUP.EXE
1. Use a Windows 10/11 64-bit computer.
2. Install 64-bit Python 3.11 and enable “Add Python to PATH”.
3. Install Tesseract OCR in the normal Program Files location.
4. Install Inno Setup 6.
5. Extract this ZIP completely.
6. Double-click: build_installer_windows.bat
7. Wait for the build to finish.
8. The final client file will be:
   release\ElectionPDFConverter_Setup.exe
9. Give only ElectionPDFConverter_Setup.exe to the client.

PORTABLE APP WITHOUT INSTALLER
- Double-click build_exe_windows.bat
- The output will be:
  release\ElectionPDFConverter\ElectionPDFConverter.exe
- Keep the complete ElectionPDFConverter folder together.

CLIENT USE
1. Install from ElectionPDFConverter_Setup.exe.
2. Open the desktop shortcut.
3. Select a PDF.
4. Click Convert PDF.
5. Download the generated Excel.

DATA LOCATION
Temporary uploads and generated workbooks are stored locally at:
%LOCALAPPDATA%\ElectionPDFConverter
No PDF is uploaded to an external server.

IMPORTANT ACCURACY NOTE
This is an OCR converter. Different government PDF layouts and scan quality can
change results. Test it with the client’s real PDFs and review orange rows before
promising perfect accuracy for every layout.
