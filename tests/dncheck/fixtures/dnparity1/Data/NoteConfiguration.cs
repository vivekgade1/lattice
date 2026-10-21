using DnParity1.Models;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace DnParity1.Data;

public class NoteConfiguration : IEntityTypeConfiguration<Note>
{
    public void Configure(EntityTypeBuilder<Note> builder)
    {
        builder.ToTable("notes");
        builder.HasIndex(n => new { n.OwnerId, n.Title }).IsUnique();
        builder.Property(n => n.Body).HasMaxLength(2000);
        builder.Property(n => n.Priority).HasDefaultValue(3);
        builder.Property(n => n.Status).HasConversion<string>().HasMaxLength(16).HasDefaultValue(NoteStatus.Draft);
        builder.HasMany(n => n.Tags).WithOne(t => t.Note).HasForeignKey(t => t.NoteId);
    }
}
