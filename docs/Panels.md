# Panels

### Splash Screen
![Splash_screen](img/panels/splash_screen.png)
!!! tip
    The *'power on'* button will only appear if a power device exists in moonraker and that power device name
    should be added to `power_devices`  in the printer section of the klipperscreen configuration file

### Main Menu
![Main Menu](img/panels/main_panel.png)

### [Job Status](Panels/Job_status.md)
```py
panel: job_status
```
[![Job Status](img/panels/job_status.png)](Panels/Job_status.md)

### [Bed Level](Panels/Screws.md)
```py
panel: bed_level
```
[![Bed Level](img/panels/bed_level.png)](Panels/Screws.md)

### Bed Mesh
```py
panel: bed_mesh
```
![Bed Mesh](img/panels/bed_mesh.png)

### Console
```py
panel: console
```
![Console](img/panels/console.png)

### Extrude
```py
panel: extrude theme:material-dark
```
![Extrude](img/panels/extrude.png)

### Fan
```py
panel: fan
```
![Fan](img/panels/fan.png)

### Fine Tune
```py
panel: fine_tune
```
![Fine Tune Panel](img/panels/fine_tune.png)

### Gcode Macros
```py
panel: gcode_macros
```
![Gcode Macros Panel](img/panels/gcode_macros.png)

### Input Shapers
```py
panel: input_shaper
```
![Limits](img/panels/input_shaper.png)

### Limits
```py
panel: limits
```
![Limits](img/panels/limits.png)

### Menu
![Menu Panel](img/panels/menu.png)

### Move
```py
panel: move
```
![Move Panel](img/panels/move.png)
!!! note
    An axis that is not homed can move with `FORCE_MOVE` when Klipper's `[force_move]` section sets
    `enable_force_move: True`, the kinematics are `cartesian` and one stepper drives the axis alone
    (no `stepper_z1`, `stepper_x1` and so on, no `dual_carriage` on it). The panel then shows an
    Unhomed moves button. While it is active, the arrows of the unhomed axes are highlighted and move
    with `FORCE_MOVE`. It turns off when tapped again, when leaving the panel, and once no such axis
    is left unhomed. Otherwise the arrows send normal moves, which Klipper refuses on an unhomed axis.
    `FORCE_MOVE` ignores the axis limits, and is refused while Klipper is busy, for example homing.
    Homed axes always use normal moves.

### [Model Viewer](Panels/Model_viewer.md)

### Network
```py
panel: network
```
![Network Panel](img/panels/network.png)

### Power
```py
panel: power
```
![Power](img/panels/power.png)

### Gcodes / Print
```py
panel: gcodes
```
![Gcodes Panel](img/panels/gcodes.png)

### Retraction
```py
panel: retraction
```
![Limits](img/panels/retraction.png)

### Settings
```py
panel: settings
```
![Settings](img/panels/settings.png)

### Spoolman
```py
panel: spoolman
```
![Spoolman](img/panels/spoolman.png)

### [Temperature](Panels/Temperature.md)
```py
panel: temperature
```
[![Temperature](img/panels/temperature.png)](Panels/Temperature.md)

### Updater
```py
panel: updater
```
![Updater Panel](img/panels/updater.png)

### [Z Calibrate](Panels/Zcalibrate.md)
```py
panel: zcalibrate
```
[![Z Calibrate](img/panels/zcalibrate.png)](Panels/Zcalibrate.md)
