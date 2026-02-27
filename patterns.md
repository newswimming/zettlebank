# Implementation Patterns

## Safe Frontmatter Manipulation
**Goal**: Modify metadata without corrupting file content or creating race conditions.
**Pattern**: Use `app.fileManager.processFrontMatter`.
**Code Reference**:
```typescript
app.fileManager.processFrontMatter(file, (frontmatter) => {
    // 1. Read existing tags
    const existing = frontmatter.tags || [];
    // 2. Append & Deduplicate
    frontmatter.tags = [...new Set([...existing, ...newTags])];
    // 3. Update Timestamp
    // Note: Obsidian exposes moment.js globally
    frontmatter.updated = window.moment().format();
});
