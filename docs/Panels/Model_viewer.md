# Model Viewer

Shows STL and G-code files from the file browser in 3D, inside the normal screen layout.

* STL files: tap the file in the file browser.
* G-code files: tap the file, then **Preview** in the print dialog.

The back button closes the viewer and frees the model.

## Gestures

| Gesture             | Action |
|---------------------|--------|
| One finger drag     | Orbit  |
| Two finger drag     | Pan    |
| Pinch               | Zoom   |

## STL

The model is placed the way a slicer places it: centred on the bed, with its lowest point on the bed.

## G-code

Extrusion moves are drawn as lines coloured by height. Travel moves are not drawn.
The slider sets the top visible layer, which is highlighted in white. The label shows the layer number and its height.

Layers follow the slicer's layer change comments: `;LAYER_CHANGE` from PrusaSlicer, SuperSlicer and OrcaSlicer, `;LAYER:` from Cura. A purge line printed before the first layer belongs to layer 1.
Files without these comments get their layers from the height of the extrusion moves, with vase mode spirals split into 0.01 mm steps.

## Bed

The bed outline comes from the printer's axis limits, with the origin marked by a red X, a green Y and a blue Z axis.
When the limits are not available, the outline follows the model.

## Requirements

* OpenGL 3.1, for example a Raspberry Pi 4 or newer with its standard graphics driver.
* The Python packages `numpy` and `PyOpenGL`, which are listed in `scripts/KlipperScreen-requirements.txt`.

## Limitations

* Arcs (G2, G3) are drawn as a straight line from start to end.
* Large G-code files take a while to load: about 11 seconds for a 49 MB file on a Raspberry Pi 4.
* Only tested on X11.
