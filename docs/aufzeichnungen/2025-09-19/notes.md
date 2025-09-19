# 2025-09-19 - Stages / Prims / Attributes

## Aufzeichnung
[Aufzeichnung](https://teams.microsoft.com/l/meetingrecap?driveId=b%21W5SPwrdB4E-dhKHfoblkm3E05Zs70HVMiaa35z5edzFs5VIxfInqSIB9mms5UjX5&driveItemId=01GFPY4W3VU6C45VCORFCKDDWR2VJ2AT3H&sitePath=https%3A%2F%2Fipolog-my.sharepoint.com%2F%3Av%3A%2Fg%2Fpersonal%2Fmichael_wagner_ipolog_ai%2FEXWnhc7UTolEoY7R1VOgT2cBm4LTmcjHQGFo_ssuI6aB6A&fileUrl=https%3A%2F%2Fipolog-my.sharepoint.com%2Fpersonal%2Fmichael_wagner_ipolog_ai%2FDocuments%2FRecordings%2FOpenUSD%2520Studierstube%2520-%29-20250919_150455-Besprechungsaufzeichnung.mp4%3Fweb%3D1&iCalUid=040000008200E00074C5B7101A82E00800000000A094E3F88A21DC01000000000000000010000000A3261B2BA5BF6B4EAEAF0A29E836DEA4&masterICalUid=040000008200E00074C5B7101A82E00800000000A094E3F88A21DC01000000000000000010000000A3261B2BA5BF6B4EAEAF0A29E836DEA4&threadId=19%3Ameeting_YzYyMzA3YjMtNThmZS00NzQzLWJlZDktOTMzMTIxMjI4MWNm%40thread.v2&organizerId=e5787333-0463-469d-ac23-18f4fb490b89&tenantId=12ca296f-457b-4db3-a62a-b58ab254170a&callId=98a47e7f-ffbe-4281-8219-d19481b55df5&threadType=Meeting&meetingType=Recurring&subType=RecapSharingLink_RecapCore)


## Hauptthemen

### Python Umgebung einrichten & aktivieren 
[NVIDIA Dokumentation](https://nvidia-omniverse.github.io/LearnOpenUSD/usdview-install-instructions.html)

#### Python venv einrichten 

```powershell
Windows PowerShell
Copyright (C) Microsoft Corporation. All rights reserved.

PS C:\usd_root> .\python\python.exe -m venv .\python-usd-venv
```

#### Python venv aktivieren 

```powershell
PS C:\usd_root> .\python-usd-venv\Scripts\activate
(python-usd-venv) PS C:\usd_root>

```

#### USD-Core installieren & testen 

```powershell

(python-usd-venv) PS C:\usd_root>pip install usd-core

(python-usd-venv) PS C:\usd_root>python -c "from pxr import Usd;print(Usd.GetVersion())"

```

Wenn ein Fehler kommt müssen vielleicht die VC redistributables installiert werden:

[x64 Redistributables](https://aka.ms/vs/17/release/vc_redist.x64.exe),
siehe 
[Microsoft Support](https://learn.microsoft.com/de-de/cpp/windows/latest-supported-vc-redist?view=msvc-170)


#### Jupyter Notebook Support installieren 


```powershell
(python-usd-venv) PS C:\usd_root> pip install ipykernel

python -m ipykernel install --user --name=python-usd-venv --display-name "OpenUSD Studierstube (python-usd-venv)"

```
Dann Visual Studio Code neu starten

#### VSCode Jupyter Extension aktivieren 
(in Extensions nach "Jupyter" suchen)

#### Test : 

View->Command Palette -> Create: New Jupyter Notebook




## Wichtige Erkenntnisse

## Hausaufgabe

Baue eine USD-Szene in dieser Art mit den Grundelementen zusammen : 

![Aufgabe](aufgabe.png)