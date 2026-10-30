import gettext

# KlipperScreen installs _ and ngettext as builtins at startup, and modules call them at run time.
gettext.NullTranslations().install(names=["ngettext"])
