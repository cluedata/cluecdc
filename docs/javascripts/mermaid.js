document$.subscribe(() => {
  mermaid.initialize({
    startOnLoad: false,
    theme: document.body.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "default",
    securityLevel: "strict",
  });
  mermaid.run({ querySelector: ".mermaid" });
});
