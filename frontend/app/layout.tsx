import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ServiceNow IT Helpdesk Agent",
  description: "CopilotKit frontend for the ServiceNow IT helpdesk AG-UI backend",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
