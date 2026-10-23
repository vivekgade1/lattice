using DnFindAct.Models;
using Microsoft.EntityFrameworkCore;

namespace DnFindAct.Data;

public class AppDb(DbContextOptions<AppDb> options) : DbContext(options)
{
    public DbSet<User> Users { get; set; } = null!;
    public DbSet<Doc> Docs { get; set; } = null!;

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Doc>().ToTable("docs");
        modelBuilder.Entity<User>().ToTable("users");
    }
}
