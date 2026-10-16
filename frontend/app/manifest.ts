import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Logu AI CFO",
    short_name: "Logu CFO",
    description: "Private financial operations control center",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f5f7fb",
    theme_color: "#0c1729",
    icons: [{ src: "/icons/logu-mark.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
