"""External catalogues (Metron, the primary source, and Comic Vine, the fallback) that the Gestión
edition form imports an issue from: one small client per catalogue, a mapper that turns their
answers into a proposal for the form (comics.catalog_sources.mapper) and the service the views call
(comics.catalog_sources.service). Kept free of imports so comics.forms can use the HTTP helpers
(the cover download) without loading the rest."""
