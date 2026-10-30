# GitHub

Slices a model from a CAD repository's GitHub Actions artifacts and prints it.
The slicing runs on another machine, [auto-slicer2](https://github.com/Tubbles/auto-slicer2), which connects to Moonraker as the agent `autoslice`. Without it the panel shows "Slicer not connected".

The panel goes through four steps, and the back button returns to the step before:

1. **Projects**: the artifacts of each repository's latest successful run, with their commit, age and size. **Refresh** asks again.
2. **Models**: the models in the chosen artifact. Their thumbnails load one at a time after the list shows.
3. **Options**: a row per setting (material, support, adhesion, quality) and the repository's rules, which replace the settings while one is chosen. A ✓ marks the options and rules that already have a gcode, and **Slice** turns into **Slice again** when the current choice has one.
4. **Job**: the slicing progress. When the gcode is done, the panel waits until it has reached the printer, then offers **Print**.

```py
panel: github
```
