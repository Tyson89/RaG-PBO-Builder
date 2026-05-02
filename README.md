# RaG PBO Builder

**Version:** 0.5.0 Beta  
**Author:** RaG Tyson  
**License:** Freeware - Proprietary / All Rights Reserved

RaG PBO Builder is a free build tool for DayZ modders.  
It helps pack, binarize, convert, sign, check, and organize DayZ addon PBOs.

---

## Main Features

- Pack selected addon folders into `.pbo` files
- Build one addon or multiple addons at once
- If the selected source root contains a `config.cpp`, it can be built as one addon
- Support PBO prefix files such as `$PBOPREFIX$`, `$prefix$`, `$PBOPREFIX$.txt`, and `$prefix$.txt`
- Binarize `.p3d` files with DayZ Tools
- Convert `config.cpp` files to `config.bin`
- Support nested `config.cpp` files inside subfolders
- Sign PBOs with `DSSignFile.exe`
- Copy the matching `.bikey` into the `Keys` folder
- Skip unchanged addons to save build time
- Use content-safe internal cache checks to avoid stale builds
- Use isolated temp folders per addon
- Keep clean `Addons` and `Keys` output folders
- Save build logs automatically
- Run preflight checks before building
- Use all available logical threads as the default for Binarize max processes
- Save and restore the window size and position

---

## Screenshots

![Main Window](screenshots/RaG_PBO_Builder.png)

![Main Window 2](screenshots/RaG_PBO_Builder2.png)

![Successful Build](screenshots/RaG_PBO_Builder3.png)

![Settings Window](screenshots/RaG_PBO_Builder4.png)

---

## Output Structure

The builder automatically creates this structure:

```txt
OutputRoot
|-- Addons
|-- Keys
```

- `.pbo` files go into `Addons`
- `.bisign` files go into `Addons`
- `.bikey` files go into `Keys`
- Existing `.bikey` files are not overwritten

---

## PBO Prefix Support

RaG PBO Builder supports addon prefix files.

Supported prefix file names:

```txt
$PBOPREFIX$
$prefix$
$PBOPREFIX$.txt
$prefix$.txt
```

If one of these files exists in the addon source folder, the first non-empty line is used as the internal PBO prefix.

Example:

```txt
RaG_BaseBuilding
```

or:

```txt
RaG\BaseBuilding
```

If no prefix file exists, the builder falls back to the addon/PBO name.

Prefix helper files are not packed into the final PBO.

---

## Build Pipeline

RaG PBO Builder can handle the main DayZ addon build steps in one place:

- Stage selected addon files
- Apply exclude patterns
- Binarize `.p3d` files when enabled
- Preserve original `.p3d` files when Binarize does not output them
- Convert root and nested `config.cpp` files to `config.bin`
- Pack the staged addon into a `.pbo`
- Sign the `.pbo`
- Copy the matching `.bikey`
- Publish the final PBO/signature set safely

Excluded `.p3d` and excluded `config.cpp` files are respected during staging, fallback checks, and config conversion.

---

## Safer Output Publishing

The tool builds into a temporary output location first.

Only after the new PBO and signatures are created successfully does the tool publish them into the final output folder.

The publish step includes:

- Backup of the current output PBO/signature set
- Validation that the backup exists before publishing starts
- Safer replacement of the PBO and signatures as one publish set
- Restore attempt if final publishing fails after the published output was modified
- No rollback deletion if backup preparation fails before publishing starts

This helps protect the last known-good build from being removed during failed builds or failed signature publication.

---

## Cache and Performance

RaG PBO Builder uses internal content-safe checks to avoid stale builds.

This helps detect file changes even when file size and modified time did not change.

Performance-related behavior:

- Content-safe checks are always active internally
- Repeated file fingerprints are cached during the current build run
- Binarize max processes defaults to all available logical threads
- Existing saved user settings are respected
- GUI log updates are batched for better responsiveness
- Unchanged addons are skipped automatically unless `Force rebuild` is enabled

---

## Preflight Check

Preflight can check your addon before packing.

It can detect:

- `config.cpp` syntax errors
- Nested `config.cpp` files
- Missing referenced files
- Missing textures
- Missing materials
- Missing models
- Missing sounds
- Readable internal `.p3d` references

Supported reference types include:

```txt
.paa
.rvmat
.p3d
.wss
.ogg
.cfg
.cpp
.hpp
.h
.emat
.edds
.ptc
```

Internal `.p3d` scanning is a best-effort scan.

---

## Temp Folder Handling

RaG PBO Builder uses isolated temp folders per addon.

Example:

```txt
Temp
|-- addons
    |-- RaG_BaseBuilding
    |   |-- staging
    |   |-- binarized
    |   |-- textures
    |
    |-- RaG_Config
        |-- staging
        |-- binarized
        |-- textures
```

`Force rebuild` only refreshes temp folders for selected addons.  
Other addon temp folders are not deleted.

The tool also includes safer temp cleanup options:

- `Clear build temp` removes only known builder temp folders
- `Clear all temp` clears the full selected temp root after confirmation and safety checks

---

## User Interface

The interface includes:

- Cleaner modern dark UI
- Grouped build options:
  - Build pipeline
  - Safety
  - Performance
- Larger main action buttons for `Build PBOs` and `Preflight`
- `Options` button in the top-right header
- `Open` buttons next to `Source root` and `Output root`
- Clear build/log/cache/temp controls
- Larger log area
- Colored log output for warnings, errors, success messages, sections, and tool-related lines
- Status badge for Ready, Building, Preflight, Done, and Error states
- Status text and progress bar
- Licence and About windows
- Version number shown in the tool
- Saved window size and position

---

## Requirements

- Windows
- DayZ Tools installed
- `binarize.exe` from DayZ Tools
- `CfgConvert.exe` from DayZ Tools
- `DSSignFile.exe` from DayZ Tools, if signing is enabled
- A `.biprivatekey` file, if signing is enabled

Python is not required when using the compiled `.exe` version.

---

## Basic Usage

1. Start `RaG_PBO_Builder.exe`
2. Select your `Source root`
3. Select your `Output root`
4. Open `Options` and check the DayZ Tools paths
5. Select your `.biprivatekey` if you want to sign PBOs
6. Select the addon or addons you want to build
7. Click `Build PBOs`

Optional:

- Use `Preflight` to check configs and referenced paths before building
- Enable `Preflight before build` if you want checks to run automatically
- Use `Force rebuild` if you want to ignore the build cache and rebuild selected addons
- Use `Clear build cache` if selected addons should be rebuilt later

---

## Important Key Warning

Never share your `.biprivatekey`.  
Only distribute the matching `.bikey`.

Your `.biprivatekey` is private and should stay on your own machine.  
The `.bikey` is the public key that can be shared with server owners or included in a mod release.

---

## Windows SmartScreen Warning

Windows may show a warning such as `Windows protected your PC` or mark the file as unsafe.

This can happen because RaG PBO Builder is a new unsigned community tool and does not use a paid Microsoft code-signing certificate. It does not automatically mean the file is malicious.

Only download RaG PBO Builder from the official GitHub release or official RaG source.

If you trust the download source, you can click:

```txt
More info -> Run anyway
```

Do not download modified versions from random reuploads.

---

## Licence

RaG PBO Builder is freeware, but it is not open source.

You may use it free of charge for personal and authorized DayZ modding purposes.

You may not sell, rent, sublicense, reupload, redistribute, modify, decompile, reverse engineer, publish, or include this software or its source code in another project without written permission from the author.

See `LICENSE.txt` for the full license text.

---

## Disclaimer

This tool is provided as-is without warranty.

The author is not responsible for damaged files, lost data, invalid PBOs, failed builds, server issues, broken signatures, leaked keys, or any other damage caused by the use or misuse of this software.
