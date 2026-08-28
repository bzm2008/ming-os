# Ming Store Spark Public Provider

Ming Store exposes a `spark-public` source adapter for the public Spark catalog.
It consumes the published category JSON for presentation only and resolves an
installable artifact only after the signed `InRelease` and `Packages` indexes,
package identity, architecture, path, and SHA256 all agree.

The adapter does not install or launch the Spark Store client, APM, ACE, aptss,
or any vendor helper. It does not scrape private APIs, redistribute private
packages, or bypass access controls. A missing or unverified Spark archive
key keeps the provider in browse-only mode.

Upstream attribution: `spark-store-project/spark-store`, licensed under
GPL-3.0-or-later. Ming Store is an independent compatibility adapter and does
not copy the Spark Store client UI or code. The public endpoints used by the
adapter are documented in the source configuration at
`assets/ming-store-catalog/spark-public.json`.
