import { expect, test } from "@playwright/test";

test("a Customer sees their phone number as a Pseudonym and reveals it at the Gateway", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Customer C001" })).toBeVisible();

  await page.getByRole("button", { name: "What phone number do you have for me?" }).click();

  // The Agent only ever had the Pseudonym; the real number comes from the Gateway on request.
  const pseudonym = page.getByRole("button", { name: "Reveal <PHONE_1>" });
  await expect(pseudonym).toBeVisible();
  await expect(page.getByText("+91 9845012376")).toHaveCount(0);

  await pseudonym.click();

  await expect(page.getByText("+91 9845012376")).toBeVisible();
});
