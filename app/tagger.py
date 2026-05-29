class Tagger:

    TAG_KEYWORDS = {
        "irn":              ["irn", "irn no", "irn number", "irn details"],
        "ack":              ["ack no", "ack date", "ack number", "acknowledgement"],
        "invoice":          ["invoice no", "invoice number", "invoice date"],
        "date-correction":  ["date to be changed", "invalid date", "wrong date", "void"],
        "file-update":      ["file no", "file number", "not updated", "not captured"],
        "approval-pending": ["approval pending", "awaiting", "still pending"]
    }

    def tag(self, text):
        """
        Detect tags from email thread text.
        Returns list of matched tag strings.
        """

        text = text.lower()
        tags = []

        for tag, keywords in self.TAG_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    tags.append(tag)
                    break  # one match per tag is enough

        return tags