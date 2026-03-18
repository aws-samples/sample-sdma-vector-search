// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
export interface SearchFilters {
  category?: string;
  subcategory?: string;
  style?: string;
  materials?: string;
  primaryColors?: string;
}

export interface CategoryConfig {
  categories: Record<string, string[]>;
  styles: string[];
  materials: string[];
  colors: string[];
}

// Default fallback values (used when API is unavailable)
export const DEFAULT_CATEGORY_CONFIG: CategoryConfig = {
  categories: {
    "Furniture": ["Chair", "Stool", "Bench", "Sofa", "Bed", "Table", "Desk", "Bookcase", "Cabinet"],
    "Kitchen": ["Appliance", "Cabinet", "Fixture"],
    "Bathroom": ["Fixture", "Cabinet"],
    "Lighting": ["Floor Lamp", "Table Lamp", "Ceiling Lamp", "Wall Lamp", "Fan"],
    "Electronics": ["Computer", "Television", "Audio"],
    "Decor": ["Plant", "Pillow", "Rug", "Coat Rack", "Book", "Toy"],
    "Architecture": ["Wall", "Door", "Floor", "Stairs"],
  },
  styles: ["Realistic", "Stylized", "LowPoly", "Modern", "Traditional", "Minimalist", "Industrial", "Scandinavian"],
  materials: ["Wood", "Metal", "Plastic", "Fabric", "Leather", "Glass", "Stone", "Ceramic", "Porcelain"],
  colors: ["Red", "Orange", "Yellow", "Green", "Blue", "Purple", "Pink", "Brown", "Black", "White", "Gray", "Beige", "Cream"],
};
