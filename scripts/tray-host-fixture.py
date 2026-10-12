"""Minimal StatusNotifierWatcher on an isolated test session bus."""
from gi.repository import Gio, GLib

xml = '''<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
<property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
<property name="RegisteredStatusNotifierItems" type="as" access="read"/>
<property name="ProtocolVersion" type="i" access="read"/>
</interface></node>'''
info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
items = []
def method(conn, sender, path, interface, name, parameters, invocation):
    if name == 'RegisterStatusNotifierItem':
        items.append(parameters.unpack()[0])
    invocation.return_value(GLib.Variant('()', ()))
def prop(conn, sender, path, interface, name):
    return {'IsStatusNotifierHostRegistered': GLib.Variant('b', True),
            'RegisteredStatusNotifierItems': GLib.Variant('as', items),
            'ProtocolVersion': GLib.Variant('i', 0)}[name]
def acquired(conn, name):
    conn.register_object('/StatusNotifierWatcher', info, method, prop, None)
def ready(conn, name):
    print('ready', flush=True)
Gio.bus_own_name(Gio.BusType.SESSION, 'org.kde.StatusNotifierWatcher',
                Gio.BusNameOwnerFlags.NONE, acquired, ready, None)
GLib.MainLoop().run()
