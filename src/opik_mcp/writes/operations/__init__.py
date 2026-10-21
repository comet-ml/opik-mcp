"""One module per entity family, holding what its write operations do.

The registry entry says where an operation goes and what it accepts; the
module here says how its payload becomes a request, what has to be resolved
before it, which backend answers mean something other than what they say, and
what the caller should be told afterwards. ``dispatch`` stays generic and
reaches all of it through the hooks on the registry entry.
"""
