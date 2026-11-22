import { describe, expect, it } from "vitest";
import { splitPseudonyms } from "./pseudonyms";

describe("a reply split into text and the Pseudonyms a viewer can reveal", () => {
  it("finds each Pseudonym between pieces of text", () => {
    expect(splitPseudonyms("Call <PHONE_1> or mail <EMAIL_12>.")).toEqual([
      { text: "Call " },
      { pseudonym: "<PHONE_1>" },
      { text: " or mail " },
      { pseudonym: "<EMAIL_12>" },
      { text: "." },
    ]);
  });

  it("keeps a type with an underscore whole", () => {
    expect(splitPseudonyms("<ACCOUNT_NUMBER_2>")).toEqual([{ pseudonym: "<ACCOUNT_NUMBER_2>" }]);
  });

  it("leaves text that only looks close alone", () => {
    expect(splitPseudonyms("a <b> tag, <PHONE_0> and <phone_1>")).toEqual([
      { text: "a <b> tag, <PHONE_0> and <phone_1>" },
    ]);
  });
});
