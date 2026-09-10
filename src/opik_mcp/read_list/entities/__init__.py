"""One namespace per entity ``read`` and ``list`` can answer for.

A module here owns everything about its entity, and ends with the
:class:`~opik_mcp.read_list.handler.EntityHandler` that ``registry`` registers.
The root of ``read_list`` keeps only what every entity shares: the two
dispatchers, the handler contract, the filter language, the window vocabulary,
compression, paging and project scope.

An entity that needs more than one file gets a package instead of a module;
the import in the registry reads the same either way.
"""
