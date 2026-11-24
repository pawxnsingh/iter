the IterApp class is made up of 8 kind of things, which is also the top level app


1. class level configurations

these are the uppercase configurations which are used to the define the overall app before it runs
and textual read these when constructing an app

these include the css_path, screens, horizontal_breakpoints, bindings, modes, etc


2. another is that reactive variable and stats

its a declarative attributes, that trigger rerender and watcher when assigned

variable likst column, column width and other things like that, terminal title, and show sessions

here two things it have "reactive" and "var" 

reactive refreshes the ui
and var is cheaper and it doesn't and its for the non visual things


3. then comes the instance setup, here the ordering matter of this __init__()

everything which is not reactive comes before the 
```super().__init__()```

and the things which are reactive comes after wards

this is for the things that we need to pass, textual app maybe the config or something related to that


4. lifecycle hooks

the textual call the method on the defined methods. the two big one here is 

on_mount() and another one is the on_load()

async def on_load(self):    # before the UI exists — load DB, read settings from disk

async def on_mount(self):   # UI is up — create the first screen, start timers


