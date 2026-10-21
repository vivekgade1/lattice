using System.ComponentModel.DataAnnotations;

namespace DnParity1.Models;

public class Tag
{
    public int Id { get; set; }

    public int NoteId { get; set; }

    public Note? Note { get; set; }

    [MaxLength(40)]
    public string Label { get; set; } = "";
}
