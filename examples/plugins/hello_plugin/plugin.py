"""Example plugin: one tool, registered through the restricted registry.

Try it:
    mkdir -p .zill/plugins && cp -r examples/plugins/hello_plugin .zill/plugins/hello
    zill plugin enable hello
    zill run "use the hello tool to greet Ada"

The model sees the tool as plugin__hello__hello. Its risk ("read") must be
listed in manifest.json's permissions, or registration fails.
"""

from zill import tool


def register(registry):
    @tool("Greet someone by name.", risk="read", name="Who to greet")
    def hello(name):
        return f"Hello, {name}! (from the hello plugin)"

    registry.add_tool(hello)
