using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;
using Microsoft.EntityFrameworkCore;

namespace DnParity1.Models;

[Index(nameof(Email), IsUnique = true)]
public class User
{
    public int Id { get; set; }

    [MaxLength(254)]
    public string Email { get; set; } = "";

    [JsonIgnore]
    [MaxLength(100)]
    public string PasswordHash { get; set; } = "";

    [MaxLength(16)]
    public string Role { get; set; } = "Member";

    public Address? Address { get; set; }

    public List<Note> Notes { get; set; } = new();
}

public class Address
{
    public string Street { get; set; } = "";

    public string City { get; set; } = "";
}
