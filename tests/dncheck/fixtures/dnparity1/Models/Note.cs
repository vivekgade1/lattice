using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

namespace DnParity1.Models;

public enum NoteStatus
{
    Draft,
    Published,
    Archived,
}

public class Note
{
    public int Id { get; set; }

    public int OwnerId { get; set; }

    public User? Owner { get; set; }

    [MaxLength(120)]
    public string Title { get; set; } = "";

    public string? Body { get; set; }

    public int Priority { get; set; }

    public NoteStatus Status { get; set; }

    [JsonIgnore]
    public string? ModerationNote { get; set; }

    [JsonPropertyName("created")]
    public DateTime CreatedAt { get; set; }

    public List<Tag> Tags { get; set; } = new();
}
