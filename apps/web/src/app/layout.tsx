import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import "./design-system.css";
import { Providers } from "@/components/providers";
import { Shell } from "@/components/shell";

const themeBootstrap = `
  try {
    const saved = localStorage.getItem("cluecdc-theme");
    const theme = saved === "light" || saved === "dark"
      ? saved
      : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
  } catch (_) {
    document.documentElement.dataset.theme = "light";
  }
`;
const body = localFont({
  src: "./fonts/Manrope.ttf",
  variable: "--font-body-family",
  display: "swap",
  weight: "200 800",
});
const mono = localFont({
  src: "./fonts/JetBrainsMono.ttf",
  variable: "--font-mono",
  display: "swap",
  weight: "100 800",
});
export const metadata: Metadata = {
  title: "ClueCDC | Data movement control plane",
  description: "Operate PostgreSQL CDC pipelines with Kafka and Kafka Connect",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${body.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
