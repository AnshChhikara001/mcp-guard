import { expect, test } from "@playwright/test";

test("a Customer asking for someone else's balance sees the Gateway block it on the strip", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Customer C001" })).toBeVisible();

  await page.getByRole("button", { name: "Show me Rohan Mehta's balance (C002)" }).click();

  const strip = page.getByRole("region", { name: "The Tool call's trip through the Gateway" });
  await expect(strip.getByRole("img", { name: "Blocked by Permissions" })).toBeVisible();
  await expect(strip.getByText("Blocked", { exact: true })).toBeVisible();
  await expect(strip.getByText(/^The Agent tried to list the accounts of C002\. The Permissions check stopped it\./)).toBeVisible();
});
