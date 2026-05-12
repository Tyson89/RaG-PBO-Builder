# RaG PBO Builder

**Version:** 0.7.17 Beta
**Author:** RaG Tyson  
**License:** Freeware - Proprietary / All Rights Reserved

RaG PBO Builder is a free build tool for DayZ modders.  
It helps pack, binarize, convert, sign, check, and organize DayZ addon PBOs.

The tool is focused on practical DayZ addon building, safe output handling, useful preflight checks, and a clean workflow for modders and mappers.

---

## Main Features

- Pack selected addon folders into `.pbo` files
- Build one addon or multiple addons at once
- If the selected Project Source contains a `config.cpp`, it can be built as one addon
- Terrain source/export work folders are not offered as addon build targets when selecting a map/project root
- Support PBO prefix files such as `$PBOPREFIX$`, `$prefix$`, `$PBOPREFIX$.txt`, and `$prefix$.txt`
- Binarize `.p3d` files with DayZ Tools
- Convert `config.cpp` files to `config.bin`
- Support nested `config.cpp` files inside subfolders
- Update missing or stale `.paa` files from newer `.png`/`.tga` source textures during builds
- Sign PBOs with `DSSignFile.exe`
- Copy the matching `.bikey` into the `Keys` folder
- Skip unchanged addons to save build time
- Use content-safe internal cache checks to avoid stale builds
- Use isolated temp folders per addon
- Keep clean `Addons` and `Keys` output folders
- Save build logs automatically
- Run configurable preflight checks before building
- Preflight checks only files that belong to the selected pack target(s) and are not excluded from the packed PBO
- Export preflight reports automatically as `.txt` and `.json`
- Use all available logical threads as the default for Binarize workers
- Save and restore the window size and position
- Save named presets for Project Source and Build Output
- Load Project Source and Build Output presets independently for faster project switching
- Use a log severity filter to hide `INFO` lines or show only warnings/errors
- Includes additional terrain and mapper-focused WRP checks
- Inspect and extract existing `.pbo` archives with the standalone `RaG_PBO_Inspector.exe`
- Drop `.pbo` files directly into the inspector window on Windows
- Convert extracted `.bin` files and rapified material files with `CfgConvert.exe`, while leaving non-config bins such as `texHeaders.bin` alone

---

## Screenshots

![Main Window](screenshots/RaG_PBO_Builder.png)

![Main Window 2](screenshots/RaG_PBO_Builder2.png)

![Successful](screenshots/RaG_PBO_Builder3.png)

![Settings](screenshots/RaG_PBO_Builder4.png)

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

## Named Path Presets

RaG PBO Builder supports named presets for Project Source and Build Output.

Project Source and Build Output presets are saved separately, so they can be mixed freely.

Example:

```txt
Project Source preset:
RaG BaseBuilding -> P:\RaG_BaseBuilding

Build Output preset:
Stable Release -> D:\RaG Releases\Stable
```

This makes it easier to switch between different projects, test builds, release folders, and upload folders without manually browsing to the same paths every time.

Preset behavior:

- Project Source presets store only Project Source paths
- Build Output presets store only Build Output paths
- Presets have custom names
- Project Source and Build Output presets can be selected independently
- Loading a Project Source preset refreshes the addon list automatically
- Loading a path preset selects all detected addons by default
- Presets do not change signing, Binarize, CfgConvert, temp, exclude, private key, or preflight settings

This keeps path switching convenient without accidentally changing important build settings.

---

## PBO Inspector / Extractor

`RaG_PBO_Inspector.exe` is a separate tool for existing `.pbo` archives.

The inspector can:

- Read the PBO header
- Show packed files in an expandable folder tree with sizes, timestamps, and packing methods
- Show the detected PBO prefix
- Accept `.pbo` files dropped directly into the window on Windows
- Preview text-like entries in a built-in read-only viewer with C/config-style syntax highlighting
- Inspect `.p3d` metadata such as ODOL/MLOD format, version, ODOL resolution-array LODs, textures, materials, proxies, and animation references
- Open the text viewer from `View selected` or by double-clicking an entry
- Reload a manually typed PBO path with `Reload PBO` or Enter
- Extract selected files or selected folders
- Extract the full archive
- Convert extracted `.bin` files to `.cpp` and rapified material files such as `.rvmat` back to readable text with DayZ Tools `CfgConvert.exe`, while skipping `texHeaders.bin`
- Refuse unsafe extract paths that try to escape the selected output folder

Stored and BI LZSS-compressed `Cprs` PBO entries are extracted or previewed; unsupported entries are listed but not extracted. `.bin` to `.cpp` conversion, `.bin` preview, and rapified material text conversion require a valid `CfgConvert.exe` path, except `texHeaders.bin`, which is left as binary data. P3D inspection is a metadata scan, not debinarization, and does not recover `model.cfg`; use Mikero DeP3d/ExtractModelCfg for that. In-window drag/drop uses `tkinterdnd2`, which is bundled into the inspector EXE by the build script.

Install Python dependencies before building from source:

```powershell
python -m pip install -r requirements.txt
```

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

Preflight also performs prefix sanity checks and can warn about suspicious prefix issues such as:

- Multiple prefix files in one addon
- Prefix paths starting with a drive path such as `P:\`
- Leading or trailing slashes
- Forward slashes in prefix paths
- Prefix paths that do not match detected terrain `worldName` paths

---

## Build Pipeline

RaG PBO Builder can handle the main DayZ addon build steps in one place:

- Stage selected addon files
- Apply exclude patterns
- Optionally update missing or stale `.paa` files from `.png`/`.tga` sources in the staging folder
- Binarize `.p3d` files when enabled
- Preserve original `.p3d` files when Binarize does not output them
- Convert root and nested `config.cpp` files to `config.bin`
- Pack the staged addon into a `.pbo`
- Sign the `.pbo`
- Copy the matching `.bikey`
- Publish the final PBO/signature set safely

`Update PAA` uses DayZ Tools `ImageToPAA.exe`. It writes converted `.paa` files into the staging folder only; source `.paa`, `.png`, and `.tga` files are not overwritten.

Excluded `.p3d` and excluded `config.cpp` files are respected during staging, fallback checks, and config conversion.

The build pipeline also performs post-step verification where applicable:

- Verify generated `config.bin` files
- Verify packed PBO output exists
- Verify published output exists after the safe publish step
- Verify signatures exist when signing is enabled
- Summarize Binarize warnings and errors in the log

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
- Binarize workers default to all available logical threads
- The default worker count is assigned automatically according to the available threads of the running system
- Existing saved user settings are respected
- GUI log updates are batched for better responsiveness
- Unchanged addons are skipped automatically unless `Force rebuild` is enabled

---

## Preflight Check

Preflight can check your addon before packing.

It is designed to catch common DayZ addon problems before a broken PBO is created.

Preflight can detect or warn about:

- `config.cpp` syntax errors
- Nested `config.cpp` files
- `CfgPatches` problems
- Missing or suspicious `requiredAddons[]`
- DayZ `CfgMods` and script module path issues
- Missing referenced files
- Referenced files that exist but are excluded from the final PBO
- Missing textures
- Missing materials
- Missing models
- Missing sounds
- RVMAT texture reference issues
- Texture freshness issues
- Readable internal `.p3d` references
- Case-only path conflicts
- Risky file names and path names
- Prefix issues
- Failed conversion or output verification

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
.shp
.dbf
.shx
.prj
```

Internal `.p3d` and `.wrp` scanning is best-effort.

Some binary files may not contain readable references. The builder avoids noisy warnings for files where no readable references are found.

---

## Preflight Line Numbers

For text-based files, missing or excluded reference messages include the relative file path and line number when possible.

Example:

```txt
ERROR: Missing referenced file in config.cpp: line 142: rag_beehive\rag_honey_pot_empty.p3d
ERROR: Missing referenced file in config\SomeFolder\config.cpp: line 37: data\missing_texture.paa
```

Line numbers are available for text-style files such as:

- `config.cpp`
- `.h`
- `.hpp`
- `.rvmat`
- `.cfg`
- `.layout`
- `.xml`
- `.json`

Binary scans, such as internal `.p3d` or `.wrp` reference scans, do not have line numbers.

---

## Config Checks

Preflight includes DayZ-focused config checks.

It can check:

- Whether `config.cpp` can be converted
- Whether `CfgPatches` exists
- Whether `CfgPatches` contains addon classes
- Whether `requiredAddons[]` is missing or suspicious
- Whether script folders exist without a matching `CfgMods` setup
- Whether `CfgMods` script module paths point to real folders

The tool does not enforce legacy `units[]` or `weapons[]` arrays.

Empty `requiredAddons[]` is not treated as automatically wrong.  
It may still trigger a soft hint when the config appears to inherit from external classes.

`CfgMods` can be detected in:

- Root `config.cpp`
- Nested `config.cpp`
- Config files included by another config

Nested configs are not expected to have their own `CfgMods`.

---

## Configurable Preflight Checks

The Options window includes configurable preflight checks.

These checks can be enabled or disabled:

- `requiredAddons` hints
- Texture freshness
- Risky path names
- Case conflicts
- P3D internal scan
- Terrain / WRP checks
- Terrain navmesh checks
- WRP internal scan
- Terrain source/export warnings
- Terrain layer checks
- 2D map config checks
- Terrain size checks

This allows the user to keep preflight strict for release builds or reduce noise during development.

---

## Preflight Reports

RaG PBO Builder automatically exports preflight reports next to the build log.

Report formats:

```txt
.txt
.json
```

The report contains a clean summary of the preflight result and collected messages.

This makes it easier to review problems after a long check or share a report with another developer.

---

## Log Filter

The log window includes a severity filter.

Available filters:

```txt
All
Hide INFO
Warnings + Errors
Errors Only
```

This is useful when a build or preflight produces a long log and you only want to focus on relevant warnings or errors.

Saved log files still contain the full output.

---

## Terrain / WRP Support

RaG PBO Builder includes mapper-focused terrain checks.

Terrain checks are automatically used when a `.wrp` file is detected.

Supported terrain checks include:

- WRP detection
- `CfgWorlds` checks
- `CfgWorldList` / `CfgWorldsList` checks
- `worldName` path validation
- Verification that `worldName` points to an existing `.wrp`
- Prefix consistency checks for terrain projects
- Duplicate or stale `.wrp` checks
- Multiple `worldName` warnings
- Optional WRP internal binary reference scan
- Terrain folder structure warnings
- Terrain source/export packing warnings
- Terrain layer/RVMAT checks
- Optional 2D map image/config checks
- Terrain addon size warnings
- Terrain size breakdown by top-level folder

Example size breakdown:

```txt
Terrain size estimate:
data        1.8 GB
world       320 MB
source      6.4 GB WARNING
navmesh     120 MB
```

CE/server mission setup is not validated by RaG PBO Builder because it is outside the terrain PBO build process.

---

## Road and Shape Reference Checks

Terrain projects can reference road or shape files.

RaG PBO Builder can check references to:

```txt
.shp
.dbf
.shx
.prj
```

For `.shp` files, the builder can also warn if important sidecar files are missing.

Example:

```txt
WARNING: Road shape file exists but matching .dbf sidecar is missing.
WARNING: Road shape file exists but matching .shx sidecar is missing.
```

These checks help catch incomplete road/shape data before packing a terrain PBO.

---

## Navmesh Checks

Terrain preflight can optionally check navmesh folders and files.

It can warn about:

- Missing navmesh folders
- Empty navmesh folders
- Navmesh files excluded by current exclude patterns
- Navmesh folders where all detected files appear to be excluded

Missing navmesh is warning-only.  
Some maps may be tested or packed without a final navmesh during development.

---

## Source and Export File Warnings

Terrain projects can contain large source and export files that usually should not be packed into a release PBO.

RaG PBO Builder can warn about:

- `source` folders
- `export` or `exports` folders
- Terrain Builder style project files
- Large terrain source images
- Heightmap, satellite, mask, and raw terrain source files
- Files that may heavily increase the final PBO size

Common source/export file types include:

```txt
.pew
.tv4p
.tv4l
.asc
.xyz
.raw
.tif
.tiff
.psd
.png
.tga
.lbt
```

These warnings are meant to help prevent accidentally packing development files.

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
- `Clear full temp` clears the full selected temp root after confirmation and safety checks

---

## User Interface

The interface includes:

- Modern graphite-style UI
- Project Source and Build Output path fields
- Independent named path presets
- Grouped build options:
  - Pipeline
  - Safety
  - Performance
- Binarize workers setting
- Larger main action buttons for `Build PBOs` and `Preflight`
- `Options` button in the top-right header
- `Open` buttons next to Project Source and Build Output
- Clear build/log/cache/temp controls
- Larger log area
- Log severity filter
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
- `ImageToPAA.exe` from DayZ Tools, if `Update PAA` is enabled
- `DSSignFile.exe` from DayZ Tools, if signing is enabled
- A `.biprivatekey` file, if signing is enabled

Python is not required when using the compiled `.exe` version.

---

## Building From Source

The source project keeps the icon in `assets/HEADONLY_SQUARE_2k.ico`.

To build the builder executable, run this from the repository root:

```powershell
.\build_rag_pbo_builder.ps1
```

The generated builder executable is written to:

```txt
dist\RaG_PBO_Builder.exe
```

To build the standalone inspector/extractor:

```powershell
.\build_rag_pbo_inspector.ps1
```

The generated inspector executable is written to:

```txt
dist\RaG_PBO_Inspector.exe
```

To make a public download package for GitHub Releases:

```powershell
.\package_release.ps1
```

This creates a versioned zip in `releases\` containing both EXEs, the README, licence, changelog, and SHA256 checksums.

Generated `build`, `dist`, log, `.exe`, and release package files are ignored by Git. Put public binaries in GitHub Releases instead of committing them to the source tree.

The repository also includes a GitHub Actions workflow. Push a tag such as `v0.7.11-beta` and GitHub will build the Windows package and attach the zip to the GitHub Release automatically.

---

## Basic Usage

1. Start `RaG_PBO_Builder.exe`
2. Select your Project Source
3. Select your Build Output
4. Open `Options` and check the DayZ Tools paths
5. Select your `.biprivatekey` if you want to sign PBOs
6. Select the addon or addons you want to build
7. Click `Build PBOs`

Optional:

- Save Project Source and Build Output presets for faster switching
- Use `Preflight` to check configs and referenced paths before building
- Enable `Preflight before build` if you want checks to run automatically
- Use the log filter to focus on warnings or errors
- Use `Force rebuild` if you want to ignore the build cache and rebuild selected addons
- Use `Clear build cache` if selected addons should be rebuilt later
- Exported preflight reports can be found next to the build log

---

## Recommended Mapper Usage

For terrain projects:

1. Select the terrain addon as Project Source
2. Check that the addon contains the expected `.wrp`
3. Run `Preflight`
4. Review `CfgWorlds`, `CfgWorldList`, and `worldName` warnings
5. Review terrain source/export warnings
6. Review terrain size breakdown
7. Build only after the preflight result looks clean

RaG PBO Builder does not validate server mission setup.  
Make sure the server mission and world configuration are handled separately.

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
